"""Agent configuration, durable run controls, and resumable owner-only SSE."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from agenthub.agents import service
from agenthub.agents.models import Agent, AgentEvent, AgentRun, AgentStep
from agenthub.agents.schemas import (
    AgentConfig,
    AgentOutput,
    ApprovalInput,
    ApprovalOutput,
    CommandName,
    EventOutput,
    RunInput,
    RunOutput,
    StepOutput,
)
from agenthub.core.errors import AppError, ErrorResponse
from agenthub.evaluations.models import EvaluationResult
from agenthub.identity.dependencies import CurrentPrincipal, DatabaseSession
from agenthub.identity.models import LoginSession, User

router = APIRouter(
    prefix="/api/v1",
    tags=["agents"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post("/agents", response_model=AgentOutput, status_code=201)
def create_agent(
    data: AgentConfig, request: Request, session: DatabaseSession, principal: CurrentPrincipal
) -> Agent:
    service.validate_config(
        session, request.app.state.settings, principal.user.id, principal.user.role_name, data
    )
    row = Agent(owner_id=principal.user.id, config=data.model_dump(mode="json"))
    session.add(row)
    session.commit()
    return row


@router.get("/agents", response_model=list[AgentOutput])
def list_agents(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[Agent]:
    return list(
        session.scalars(
            select(Agent)
            .where(Agent.owner_id == principal.user.id)
            .order_by(Agent.created_at.desc(), Agent.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/agents/{agent_id}", response_model=AgentOutput)
def get_agent(agent_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> Agent:
    return service.owned_agent(session, agent_id, principal.user.id)


@router.put("/agents/{agent_id}", response_model=AgentOutput)
def replace_agent(
    agent_id: UUID,
    data: AgentConfig,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> Agent:
    row = service.owned_agent(session, agent_id, principal.user.id, lock=True)
    service.validate_config(
        session, request.app.state.settings, principal.user.id, principal.user.role_name, data
    )
    row.config = data.model_dump(mode="json")
    session.commit()
    return row


@router.post("/agents/{agent_id}/runs", response_model=RunOutput, status_code=202)
def start_run(
    agent_id: UUID,
    data: RunInput,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> AgentRun:
    row = service.owned_agent(session, agent_id, principal.user.id)
    return service.start_run(
        session,
        row,
        principal.user.id,
        principal.user.role_name,
        request.app.state.settings,
        data,
        request_id=request.state.request_id,
    )


@router.get("/agent-runs", response_model=list[RunOutput])
def list_runs(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[AgentRun]:
    return list(
        session.scalars(
            select(AgentRun)
            .where(AgentRun.owner_id == principal.user.id)
            .order_by(AgentRun.created_at.desc(), AgentRun.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/agent-runs/{run_id}", response_model=RunOutput)
def get_run(run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> AgentRun:
    return service.owned_run(session, run_id, principal.user.id)


@router.get("/agent-runs/{run_id}/steps", response_model=list[StepOutput])
def list_steps(
    run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> list[AgentStep]:
    service.owned_run(session, run_id, principal.user.id)
    return list(
        session.scalars(
            select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.ordinal)
        )
    )


def reject_evaluation_control(session: Session, run_id: UUID) -> None:
    if session.scalar(
        select(EvaluationResult.run_id).where(EvaluationResult.agent_run_id == run_id)
    ):
        raise AppError(409, "agent_run_managed_by_evaluation", "Use the evaluation run to cancel")


@router.post("/agent-runs/{run_id}/actions/{command}", response_model=RunOutput)
def control_run(
    run_id: UUID, command: CommandName, session: DatabaseSession, principal: CurrentPrincipal
) -> AgentRun:
    run = service.owned_run(session, run_id, principal.user.id, lock=True)
    reject_evaluation_control(session, run.id)
    return service.control(session, run, command)


@router.get("/agent-runs/{run_id}/approvals/{approval_id}", response_model=ApprovalOutput)
def get_approval(
    run_id: UUID, approval_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> ApprovalOutput:
    run = service.owned_run(session, run_id, principal.user.id)
    approval, step = service.approval_details(session, run, approval_id)
    return service.approval_output(run, approval, step)


@router.post("/agent-runs/{run_id}/approvals/{approval_id}", response_model=ApprovalOutput)
def decide_approval(
    run_id: UUID,
    approval_id: UUID,
    data: ApprovalInput,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> ApprovalOutput:
    run = service.owned_run(session, run_id, principal.user.id, lock=True)
    reject_evaluation_control(session, run.id)
    return service.decide_approval(session, run, approval_id, data)


def event_batch(
    factory: sessionmaker[Session], run_id: UUID, owner: UUID, login_id: UUID, after: int
) -> tuple[list[EventOutput], int | None]:
    with factory() as session:
        valid_login = session.scalar(
            select(LoginSession.id)
            .join(User, User.id == LoginSession.user_id)
            .where(
                LoginSession.id == login_id,
                User.id == owner,
                User.is_active,
                LoginSession.revoked_at.is_(None),
                LoginSession.expires_at > service.db_now(session),
            )
        )
        if valid_login is None:
            raise AppError(401, "invalid_session", "Authentication required")
        run = service.owned_run(session, run_id, owner)
        rows = session.scalars(
            select(AgentEvent)
            .where(
                AgentEvent.run_id == run_id,
                AgentEvent.sequence > after,
                AgentEvent.sequence <= run.event_seq,
            )
            .order_by(AgentEvent.sequence)
            .limit(100)
        )
        closed_at = (
            run.event_seq
            if run.status in service.TERMINAL | {"paused", "waiting_approval"}
            else None
        )
        return [EventOutput.model_validate(row) for row in rows], closed_at


@router.get(
    "/agent-runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    run_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
    last_event_id: Annotated[int, Header(ge=0, le=2_147_483_647)] = 0,
) -> StreamingResponse:
    owner, login_id = principal.user.id, principal.session_id
    run = await run_in_threadpool(service.owned_run, session, run_id, owner)
    if last_event_id > run.event_seq:
        raise AppError(409, "agent_event_cursor_invalid", "Event cursor is ahead of this run")
    await run_in_threadpool(session.rollback)
    factory = request.app.state.session_factory

    async def stream() -> AsyncIterator[str]:
        cursor = last_event_id
        for _ in range(60):
            if await request.is_disconnected():
                return
            try:
                batch, closed_at = await run_in_threadpool(
                    event_batch, factory, run_id, owner, login_id, cursor
                )
            except AppError:
                yield 'event: error\ndata: {"code":"invalid_session"}\n\n'
                return
            except (InterfaceError, OperationalError, DatabaseTimeoutError):
                yield 'event: error\ndata: {"code":"database_unavailable"}\n\n'
                return
            for item in batch:
                cursor = item.sequence
                yield f"id: {cursor}\nevent: {item.kind}\ndata: {item.model_dump_json()}\n\n"
            if closed_at is not None and cursor >= closed_at:
                return
            if not batch:
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
