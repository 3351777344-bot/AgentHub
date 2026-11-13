"""Persist intent before I/O and reuse the recorded result after checkpoint replay."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agenthub.agents import service
from agenthub.agents.models import AgentApproval, AgentRun, AgentStep
from agenthub.agents.schemas import AgentConfig
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.identity.models import User
from agenthub.knowledge.service import owned_kb
from agenthub.rag.schemas import AskRequest
from agenthub.tools import executor
from agenthub.tools.contracts import RESULT_BYTES, digest
from agenthub.tools.models import ToolCall, ToolRegistration
from agenthub.tools.schemas import ToolDescription, ToolName


class Paused(Exception):
    pass


class ClaimLost(Exception):
    pass


@dataclass(frozen=True)
class ModelStart:
    step: AgentStep
    tools: list[ToolDescription]
    max_tokens: int


class Ledger:
    def __init__(self, factory: sessionmaker[Session], settings: Settings, run: AgentRun) -> None:
        self.factory = factory
        self.settings = settings
        self.run = run
        self.config = AgentConfig.model_validate(run.config)

    @contextmanager
    def transaction(self) -> Iterator[tuple[Session, AgentRun]]:
        with self.factory.begin() as session:
            run = service.owned_run(session, self.run.id, self.run.owner_id, lock=True)
            if run.claim_id != self.run.claim_id or run.status != "running":
                raise ClaimLost
            yield session, run

    def guard(self, session: Session, run: AgentRun, ordinal: int) -> str:
        if service.remaining_ms(run, service.db_now(session)) <= 0:
            raise AppError(409, "agent_time_budget", "Agent time budget exhausted")
        if run.pause_requested:
            raise Paused
        if ordinal > self.config.max_steps:
            raise AppError(409, "agent_step_budget", "Agent step budget exhausted")
        if run.completion_tokens >= self.config.max_completion_tokens:
            raise AppError(409, "agent_token_budget", "Agent completion token budget exhausted")
        role = session.scalar(select(User.role_name).where(User.id == run.owner_id, User.is_active))
        if role is None:
            raise AppError(403, "agent_owner_disabled", "Agent owner is disabled")
        return role

    @staticmethod
    def existing(session: Session, run: AgentRun, ordinal: int, kind: str) -> AgentStep | None:
        step = session.scalar(
            select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.ordinal == ordinal)
        )
        if step and step.kind != kind:
            raise AppError(409, "agent_replay_conflict", "Persisted step does not match workflow")
        return step

    @staticmethod
    def add_step(
        session: Session, run: AgentRun, ordinal: int, kind: str, data: dict[str, Any] | None
    ) -> AgentStep:
        step = AgentStep(run_id=run.id, ordinal=ordinal, kind=kind, status="planned", input=data)
        session.add(step)
        session.flush()
        service.event(session, run, "step_planned", ordinal=ordinal, step_kind=kind)
        return step

    @staticmethod
    def begin(session: Session, run: AgentRun, step: AgentStep) -> None:
        step.status = "started"
        service.event(session, run, "step_started", ordinal=step.ordinal, step_kind=step.kind)

    @staticmethod
    def finish(
        session: Session,
        run: AgentRun,
        step: AgentStep,
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        step.status, step.output, step.error_code = status, output, error
        step.finished_at = service.db_now(session)
        service.event(
            session, run, "step_finished", ordinal=step.ordinal, status=status, error_code=error
        )

    def model_start(self, ordinal: int) -> ModelStart:
        with self.transaction() as (session, run):
            step = self.existing(session, run, ordinal, "model")
            if step is not None:
                return ModelStart(step, [], 0)
            role = self.guard(session, run, ordinal)
            if service.configured_model(self.settings, self.config.model_name) != run.model_target:
                raise AppError(409, "agent_model_changed", "Agent model target changed")
            for kb in self.config.knowledge_base_ids:
                owned_kb(session, kb, run.owner_id)
            tools = [
                plan.definition
                for plan in service.tools_for(session, self.settings, role, self.config)
            ]
            step = self.add_step(session, run, ordinal, "model", None)
            self.begin(session, run, step)
            max_tokens = min(
                self.config.max_completion_tokens - run.completion_tokens,
                self.settings.model_max_tokens,
            )
            return ModelStart(step, tools, max_tokens)

    def plan_tool(
        self, ordinal: int, tool: ToolName, version: int, arguments: dict[str, Any]
    ) -> UUID | None:
        with self.transaction() as (session, run):
            previous = self.existing(session, run, ordinal, "tool")
            if previous:
                approval = session.scalar(
                    select(AgentApproval).where(AgentApproval.step_id == previous.id)
                )
                return approval.id if approval else None
            role = self.guard(session, run, ordinal)
            row = session.get(ToolRegistration, (tool, version), with_for_update={"read": True})
            plan = executor.plan_call(
                session, self.settings, role, tool, version, agent=True, row=row
            )
            executor.validate_arguments(plan, arguments)
            if tool == "knowledge_search":
                kb = AskRequest.model_validate(arguments).knowledge_base_id
                if kb not in self.config.knowledge_base_ids:
                    raise AppError(
                        403, "agent_knowledge_forbidden", "Knowledge base is not allowed"
                    )
                owned_kb(session, kb, run.owner_id)
            step = self.add_step(
                session,
                run,
                ordinal,
                "tool",
                {
                    "tool": {"name": tool, "version": version},
                    "arguments": arguments,
                    "effect": plan.definition.effect,
                    "fingerprint": plan.fingerprint(),
                },
            )
            if plan.definition.effect == "read_only":
                return None
            # Bind the stored JSONB value. PostgreSQL can normalize numeric spellings.
            session.refresh(step, ["input"])
            approval = AgentApproval(step_id=step.id, digest=self.approval_digest(run, step))
            session.add(approval)
            session.flush()
            return approval.id

    @staticmethod
    def approval_digest(run: AgentRun, step: AgentStep) -> str:
        return digest(
            {"run": str(run.id), "step": str(step.id), "intent": step.input}, RESULT_BYTES
        )

    def reject_tool(self, ordinal: int, error: str) -> None:
        with self.transaction() as (session, run):
            if self.existing(session, run, ordinal, "tool") is not None:
                return
            self.guard(session, run, ordinal)
            step = self.add_step(session, run, ordinal, "tool", None)
            self.finish(session, run, step, "rejected", None, error)

    def tool_start(self, ordinal: int) -> tuple[AgentStep, executor.AgentInvocation | None]:
        with self.transaction() as (session, run):
            step = self.existing(session, run, ordinal, "tool")
            assert step is not None
            if step.status != "planned":
                return step, None
            self.guard(session, run, ordinal)
            assert step.input is not None
            approved = False
            if step.input["effect"] != "read_only":
                approval = session.scalar(
                    select(AgentApproval).where(AgentApproval.step_id == step.id)
                )
                if (
                    approval is None
                    or approval.decided_by != run.owner_id
                    or approval.digest != self.approval_digest(run, step)
                ):
                    raise AppError(409, "agent_approval_invalid", "Approval is missing or invalid")
                if approval.decision == "reject":
                    self.finish(session, run, step, "rejected", None, "agent_approval_rejected")
                    return step, None
                approved = approval.decision == "approve"
                if not approved:
                    raise AppError(409, "agent_approval_invalid", "Approval is missing or invalid")
            self.begin(session, run, step)
            assert run.claim_id is not None
            return step, executor.AgentInvocation(
                step.id, run.id, run.claim_id, step.input["fingerprint"], approved
            )

    def save_result(
        self,
        step_id: UUID,
        status: str,
        output: dict[str, Any] | None,
        error: str | None,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        with self.transaction() as (session, run):
            step = session.get(AgentStep, step_id)
            assert step is not None and step.run_id == run.id and step.status == "started"
            if step.kind == "tool" and session.get(ToolCall, step.id) is not None:
                step.tool_call_id = step.id
            run.prompt_tokens += prompt_tokens
            run.completion_tokens += completion_tokens
            self.finish(session, run, step, status, output, error)
