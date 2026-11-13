"""Owner-scoped API transactions and durable transitions shared with the worker."""

import hashlib
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agenthub.agents.models import Agent, AgentApproval, AgentEvent, AgentRun, AgentStep
from agenthub.agents.schemas import (
    AgentConfig,
    ApprovalInput,
    ApprovalOutput,
    CommandName,
    RunInput,
)
from agenthub.chat.service import owned as owned_conversation
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.knowledge.service import owned_kb
from agenthub.tools.executor import Plan, plan_call
from agenthub.tools.models import ToolCall, ToolRegistration

WORKFLOW_VERSION = 1
TERMINAL = {"completed", "failed", "cancelled"}


def configured_model(settings: Settings, name: str) -> str:
    if not settings.model_name or not settings.model_base_url:
        raise AppError(503, "model_not_configured", "Model service is not configured")
    if name != settings.model_name:
        raise AppError(422, "agent_model_unavailable", "Model is not available")
    return hashlib.sha256(f"{settings.model_base_url}\n{name}".encode()).hexdigest()


def tools_for(session: Session, settings: Settings, role: str, config: AgentConfig) -> list[Plan]:
    plans = []
    for tool in sorted(config.tools, key=lambda item: (item.name, item.version)):
        row = session.get(
            ToolRegistration, (tool.name, tool.version), with_for_update={"read": True}
        )
        plans.append(
            plan_call(session, settings, role, tool.name, tool.version, agent=True, row=row)
        )
    return plans


def validate_config(
    session: Session, settings: Settings, owner: UUID, role: str, config: AgentConfig
) -> str:
    target = configured_model(settings, config.model_name)
    for kb in config.knowledge_base_ids:
        owned_kb(session, kb, owner)
    if not config.knowledge_base_ids and any(
        tool.name == "knowledge_search" for tool in config.tools
    ):
        raise AppError(
            422, "agent_knowledge_required", "Knowledge search needs an allowed knowledge base"
        )
    tools_for(session, settings, role, config)
    return target


def owned_agent(session: Session, agent_id: UUID, owner: UUID, *, lock: bool = False) -> Agent:
    query = select(Agent).where(Agent.id == agent_id, Agent.owner_id == owner)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError(404, "agent_not_found", "Agent not found")
    return row


def owned_run(session: Session, run_id: UUID, owner: UUID, *, lock: bool = False) -> AgentRun:
    query = select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == owner)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError(404, "agent_run_not_found", "Agent run not found")
    return row


def db_now(session: Session) -> datetime:
    return cast(datetime, session.scalar(select(func.clock_timestamp())))


def event(session: Session, run: AgentRun, kind: str, **data: Any) -> None:
    # Callers serialize writes via the run row. The cursor is gap-free per run.
    run.event_seq += 1
    run.updated_at = db_now(session)
    session.add(AgentEvent(run_id=run.id, sequence=run.event_seq, kind=kind, data=data))


def start_run(
    session: Session, agent: Agent, owner: UUID, role: str, settings: Settings, data: RunInput
) -> AgentRun:
    config = AgentConfig.model_validate(agent.config)
    target = validate_config(session, settings, owner, role, config)
    if data.conversation_id is not None:
        owned_conversation(session, data.conversation_id, owner)
    run = AgentRun(
        owner_id=owner,
        agent_id=agent.id,
        conversation_id=data.conversation_id,
        config=agent.config,
        model_target=target,
        workflow_version=WORKFLOW_VERSION,
        prompt=data.prompt,
        status="queued",
        pause_requested=False,
        recoverable=False,
        remaining_ms=config.max_seconds * 1000,
        completion_tokens=0,
        prompt_tokens=0,
        event_seq=0,
    )
    session.add(run)
    session.flush()
    event(session, run, "queued")
    session.commit()
    return run


def remaining_ms(run: AgentRun, now: datetime) -> int:
    if run.active_since is None:
        return run.remaining_ms
    return max(0, run.remaining_ms - max(0, int((now - run.active_since).total_seconds() * 1000)))


def transition(
    session: Session,
    run: AgentRun,
    status: str,
    *,
    error: str | None = None,
    recoverable: bool = False,
    approval_id: UUID | None = None,
    answer: str | None = None,
) -> None:
    run.remaining_ms = remaining_ms(run, db_now(session))
    run.active_since = None
    run.claim_id = None
    run.pause_requested = False
    run.approval_id = approval_id
    run.answer = answer
    run.error_code = error
    run.recoverable = recoverable
    run.status = status
    data: dict[str, Any] = {}
    if error:
        data.update(error_code=error, recoverable=recoverable)
    if approval_id:
        data["approval_id"] = str(approval_id)
    event(session, run, status, **data)


def mark_started_uncertain(session: Session, run: AgentRun) -> bool:
    steps = list(
        session.scalars(
            select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.status == "started")
        )
    )
    for step in steps:
        if step.kind == "tool" and session.get(ToolCall, step.id) is not None:
            step.tool_call_id = step.id
        step.status = "uncertain"
        step.error_code = f"{step.kind}_outcome_unknown"
        step.finished_at = db_now(session)
        event(
            session,
            run,
            "step_finished",
            ordinal=step.ordinal,
            status=step.status,
            error_code=step.error_code,
        )
    return bool(steps)


def control(session: Session, run: AgentRun, command: CommandName) -> AgentRun:
    if command == "cancel" and (run.status not in TERMINAL or run.recoverable):
        mark_started_uncertain(session, run)
        transition(session, run, "cancelled")
    elif command == "cancel" and run.status == "cancelled":
        return run
    elif command == "pause" and run.status == "queued":
        transition(session, run, "paused")
    elif command == "pause" and run.status == "running":
        if not run.pause_requested:
            run.pause_requested = True
            event(session, run, "pause_requested")
    elif command == "resume" and (run.status == "paused" or run.recoverable):
        transition(session, run, "queued")
    else:
        raise AppError(
            409, "agent_run_state_conflict", "Operation is unavailable in this run state"
        )
    session.commit()
    return run


def approval_details(
    session: Session, run: AgentRun, approval_id: UUID
) -> tuple[AgentApproval, AgentStep]:
    pair = session.execute(
        select(AgentApproval, AgentStep)
        .join(AgentStep)
        .where(AgentApproval.id == approval_id, AgentStep.run_id == run.id)
    ).one_or_none()
    if pair is None:
        raise AppError(404, "agent_approval_not_found", "Approval not found")
    return pair[0], pair[1]


def approval_output(run: AgentRun, approval: AgentApproval, step: AgentStep) -> ApprovalOutput:
    assert step.input is not None
    return ApprovalOutput.model_validate(
        {
            "id": approval.id,
            "run_id": run.id,
            "step_id": step.id,
            "digest": approval.digest,
            "tool": step.input["tool"],
            "effect": step.input["effect"],
            "arguments": step.input["arguments"],
            "decision": approval.decision,
            "decided_at": approval.decided_at,
        }
    )


def decide_approval(
    session: Session, run: AgentRun, approval_id: UUID, data: ApprovalInput
) -> ApprovalOutput:
    approval, step = approval_details(session, run, approval_id)
    if (
        run.status != "waiting_approval"
        or run.approval_id != approval.id
        or approval.decision is not None
        or approval.digest != data.digest
    ):
        raise AppError(409, "agent_approval_conflict", "Approval is stale or does not match")
    approval.decision = data.decision
    approval.decided_by = run.owner_id
    approval.decided_at = db_now(session)
    event(session, run, "approval_decided", approval_id=str(approval.id), decision=data.decision)
    transition(session, run, "queued")
    session.commit()
    return approval_output(run, approval, step)
