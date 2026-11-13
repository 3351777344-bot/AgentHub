"""Authorize, audit and execute one explicit tool version for a platform user."""

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import anyio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from agenthub.agents.models import AgentRun, AgentStep
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.identity.models import User
from agenthub.rag.schemas import AskRequest, RetrievalStats, SearchOutput
from agenthub.rag.search import retrieve
from agenthub.tools import mcp, registry
from agenthub.tools.contracts import ARGUMENT_BYTES, RESULT_BYTES, bounded_json, digest, valid
from agenthub.tools.models import McpService, ToolCall, ToolRegistration
from agenthub.tools.schemas import AddInput, CallOutput, IntegerTotal, Status, ToolDescription


@dataclass(frozen=True)
class Plan:
    definition: ToolDescription
    endpoint: mcp.Endpoint | None = None
    registration: ToolRegistration | None = None

    def fingerprint(self) -> str:
        return digest(
            {
                "definition": self.definition.model_dump(mode="json"),
                "target": self.endpoint.fingerprint if self.endpoint else None,
                "remote": self.registration.remote_name if self.registration else None,
                "remote_digest": (
                    self.registration.definition_digest if self.registration else None
                ),
            },
            RESULT_BYTES,
        )


@dataclass(frozen=True)
class AgentInvocation:
    audit_id: UUID
    run_id: UUID
    claim_id: UUID
    fingerprint: str
    approved: bool


def plan_call(
    session: Session,
    settings: Settings,
    role: str,
    name: str,
    version: int,
    *,
    agent: bool,
    row: ToolRegistration | None,
) -> Plan:
    definition = registry.BUILTINS.get((name, version)) if row is None else registry.describe(row)
    if definition is None:
        raise AppError(404, "tool_not_found", "Tool version not found")
    if definition.required_role == "admin" and role != "admin":
        raise AppError(403, "tool_forbidden", "Tool is forbidden for this user")
    if definition.effect != "read_only" and (not agent or row is None or not row.agent_enabled):
        raise AppError(403, "tool_effect_blocked", "Tool side effects are blocked")
    endpoint = None
    if row is not None:
        if definition.effect == "read_only" and not row.enabled:
            raise AppError(403, "tool_disabled", "Tool is disabled")
        service = session.get(McpService, row.service_key, with_for_update={"read": True})
        assert service is not None  # Protected by the registration's foreign key.
        endpoint = registry.endpoint_for(settings, service)
    return Plan(definition, endpoint, row)


def validate_arguments(plan: Plan, arguments: dict[str, Any]) -> None:
    try:
        bounded_json(arguments, ARGUMENT_BYTES)
        if not valid(plan.definition.input_schema, arguments):
            raise ValueError
    except (ValueError, RecursionError):
        raise AppError(422, "tool_invalid_arguments", "Invalid tool arguments") from None


def start_call(
    factory: sessionmaker[Session],
    settings: Settings,
    actor_id: UUID,
    request_id: str,
    name: str,
    version: int,
    invocation: AgentInvocation | None = None,
) -> tuple[UUID, Plan | AppError]:
    with factory() as session:
        if invocation is not None:
            run = session.get(AgentRun, invocation.run_id, with_for_update=True)
            step = session.get(AgentStep, invocation.audit_id)
            if (
                run is None
                or run.owner_id != actor_id
                or run.status != "running"
                or run.claim_id != invocation.claim_id
                or step is None
                or step.run_id != run.id
                or step.status != "started"
            ):
                raise AppError(409, "agent_claim_lost", "Agent execution claim changed")
        role = session.scalar(select(User.role_name).where(User.id == actor_id, User.is_active))
        if role is None:
            raise AppError(401, "invalid_session", "Authentication required")
        row = session.get(ToolRegistration, (name, version), with_for_update={"read": True})
        definition = (
            registry.BUILTINS.get((name, version)) if row is None else registry.describe(row)
        )
        audit = ToolCall(
            id=invocation.audit_id if invocation else None,
            actor_id=actor_id,
            request_id=request_id,
            status="started",
            tool_name=name if definition is not None else None,
            tool_version=version if definition is not None else None,
            service_key=row.service_key if row is not None else None,
        )
        try:
            authorized = plan_call(
                session, settings, role, name, version, agent=invocation is not None, row=row
            )
            if invocation is not None:
                if authorized.definition.effect != "read_only" and not invocation.approved:
                    raise AppError(403, "tool_approval_required", "Tool approval required")
                if authorized.fingerprint() != invocation.fingerprint:
                    raise AppError(409, "tool_plan_changed", "Tool definition or target changed")
            plan: Plan | AppError = authorized
        except AppError as error:
            plan = error
        # Commit before any handler runs. Row locks and connections end here.
        session.add(audit)
        session.commit()
        return audit.id, plan


def finish_call(
    factory: sessionmaker[Session],
    call_id: UUID,
    status: Status,
    error_code: str | None,
    started: float,
) -> None:
    with factory.begin() as session:
        audit = session.get(ToolCall, call_id)
        assert audit is not None
        audit.status = status
        audit.error_code = error_code
        audit.finished_at = datetime.now(UTC)
        audit.duration_ms = int((time.monotonic() - started) * 1000)


async def run_builtin(
    name: str,
    arguments: dict[str, Any],
    actor_id: UUID,
    settings: Settings,
    factory: sessionmaker[Session],
    provider: EmbeddingProvider,
) -> dict[str, Any]:
    if name == "add_integers":
        values = AddInput.model_validate(arguments)
        return IntegerTotal(total=values.left + values.right).model_dump(mode="json")

    assert name == "knowledge_search"
    data = AskRequest.model_validate(arguments)
    stats = RetrievalStats()
    hits = await retrieve(
        factory, provider, settings, data.knowledge_base_id, actor_id, data, stats, verify=True
    )
    return SearchOutput(hits=hits, retrieval=stats).model_dump(mode="json")


async def execute(
    factory: sessionmaker[Session],
    settings: Settings,
    provider: EmbeddingProvider,
    actor_id: UUID,
    request_id: str,
    name: str,
    version: int,
    arguments: dict[str, Any],
    *,
    invocation: AgentInvocation | None = None,
) -> CallOutput:
    started = time.monotonic()
    with anyio.CancelScope(shield=True):
        call_id, plan = await run_in_threadpool(
            start_call, factory, settings, actor_id, request_id, name, version, invocation
        )
    status: Status = "failed"
    error_code: str | None = "tool_execution_failed"
    try:
        if isinstance(plan, AppError):
            raise plan
        validate_arguments(plan, arguments)

        if plan.registration is None:
            async with asyncio.timeout(plan.definition.timeout_seconds):
                result = await run_builtin(name, arguments, actor_id, settings, factory, provider)
        else:
            assert plan.endpoint is not None
            result = await mcp.call(
                settings,
                plan.endpoint,
                plan.registration.remote_name,
                plan.registration.definition_digest,
                arguments,
                plan.definition.timeout_seconds,
            )
        try:
            bounded_json(result, RESULT_BYTES)
            if not valid(plan.definition.output_schema, result):
                raise ValueError
        except (ValueError, RecursionError):
            raise AppError(502, "tool_invalid_result", "Invalid tool result") from None
        status, error_code = "completed", None
        return CallOutput(call_id=call_id, result=result)
    except ValidationError:
        status, error_code = "rejected", "tool_invalid_arguments"
        raise AppError(422, error_code, "Invalid tool arguments") from None
    except TimeoutError:
        error_code = "tool_timeout"
        raise AppError(504, error_code, "Tool call timed out") from None
    except AppError as error:
        status = "rejected" if error.status_code in (403, 404, 409, 422) else "failed"
        error_code = error.code
        raise
    except (OperationalError, InterfaceError, DatabaseTimeoutError):
        error_code = "database_unavailable"
        raise
    except asyncio.CancelledError:
        status, error_code = "cancelled", "tool_cancelled"
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(finish_call, factory, call_id, status, error_code, started)
