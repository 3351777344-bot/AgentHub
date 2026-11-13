"""Independent PostgreSQL-polled worker. API and SSE disconnects leave it running."""

import asyncio
import logging
import math
import sys
from uuid import UUID, uuid4

import httpx2
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command
from langsmith import tracing_context
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from sqlalchemy import exists, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from agenthub.agents import service, workflow
from agenthub.agents.ledger import ClaimLost, Ledger, Paused
from agenthub.agents.models import AgentRun
from agenthub.agents.provider import AgentProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.core.logging import configure_logging
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.evaluations.models import EvaluationResult

logger = logging.getLogger("agenthub.agents")


async def worker_connection(settings: Settings) -> AsyncConnection[DictRow]:
    return await AsyncConnection.connect(
        host=settings.database_host,
        port=settings.database_port,
        dbname=settings.database_name,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        connect_timeout=max(2, math.ceil(settings.dependency_timeout_seconds)),
        options="-c statement_timeout=10000 -c lock_timeout=5000",
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )


class Worker:
    def __init__(
        self, settings: Settings, factory: sessionmaker[Session], client: httpx2.AsyncClient
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.provider = AgentProvider(settings, client)
        self.embeddings = EmbeddingProvider(settings, client)

    def candidates(self) -> list[UUID]:
        with self.factory() as session:
            return list(
                session.scalars(
                    select(AgentRun.id)
                    .where(
                        AgentRun.status.in_(["queued", "running"]),
                        ~exists().where(EvaluationResult.agent_run_id == AgentRun.id),
                    )
                    .order_by(AgentRun.updated_at, AgentRun.id)
                )
            )

    def claim(self, run_id: UUID) -> AgentRun | None:
        with self.factory.begin() as session:
            run = session.get(AgentRun, run_id, with_for_update=True)
            if run is None or run.status not in ("queued", "running"):
                return None
            if run.status == "running":
                # An abandoned claim is only inspected after obtaining its advisory lock.
                unknown = service.mark_started_uncertain(session, run)
                expired = service.remaining_ms(run, service.db_now(session)) == 0
                service.transition(
                    session,
                    run,
                    "failed",
                    error="agent_outcome_unknown"
                    if unknown
                    else ("agent_time_budget" if expired else "agent_worker_interrupted"),
                    recoverable=not (unknown or expired),
                )
                return None
            if run.remaining_ms == 0:
                service.transition(session, run, "failed", error="agent_time_budget")
                return None
            run.status = "running"
            run.claim_id = uuid4()
            run.active_since = service.db_now(session)
            service.event(session, run, "running")
            return run

    def decision(self, run: AgentRun, approval_id: UUID) -> str | None:
        with self.factory() as session:
            current = service.owned_run(session, run.id, run.owner_id)
            if current.claim_id != run.claim_id:
                raise ClaimLost
            approval, _ = service.approval_details(session, current, approval_id)
            return approval.decision

    def finish(
        self,
        run: AgentRun,
        status: str,
        *,
        error: str | None = None,
        approval_id: UUID | None = None,
        answer: str | None = None,
        recoverable: bool = False,
    ) -> None:
        with self.factory.begin() as session:
            current = service.owned_run(session, run.id, run.owner_id, lock=True)
            if current.claim_id != run.claim_id:
                return
            if status == "failed" and service.mark_started_uncertain(session, current):
                recoverable = False
            if status == "waiting_approval":
                assert approval_id is not None
                approval, _ = service.approval_details(session, current, approval_id)
                assert approval.decision is None
            if recoverable and service.remaining_ms(current, service.db_now(session)) <= 0:
                status, error, recoverable = "failed", "agent_time_budget", False
            service.transition(
                session,
                current,
                status,
                error=error,
                approval_id=approval_id,
                answer=answer,
                recoverable=recoverable,
            )

    def claim_current(self, run: AgentRun) -> bool:
        with self.factory() as session:
            return (
                session.scalar(select(AgentRun.claim_id).where(AgentRun.id == run.id))
                == run.claim_id
            )

    def active_budget(self, run: AgentRun) -> float:
        with self.factory() as session:
            current = service.owned_run(session, run.id, run.owner_id)
            if current.claim_id != run.claim_id:
                raise ClaimLost
            return service.remaining_ms(current, service.db_now(session)) / 1000

    async def watch_cancellation(self, run: AgentRun, task: asyncio.Task[None]) -> None:
        while not task.done():
            await asyncio.sleep(0.25)
            if not await run_in_threadpool(self.claim_current, run):
                task.cancel()
                return

    async def execute_graph(self, run: AgentRun, saver: AsyncPostgresSaver) -> None:
        ledger = Ledger(self.factory, self.settings, run)
        config: RunnableConfig = {
            "configurable": {"thread_id": str(run.thread_id)},
            "recursion_limit": 3 * ledger.config.max_steps + 10,
        }
        try:
            if run.workflow_version != service.WORKFLOW_VERSION:
                raise AppError(409, "agent_workflow_unsupported", "Workflow version unavailable")
            graph = workflow.build_graph(ledger, self.provider, self.embeddings, saver)
            budget = await run_in_threadpool(self.active_budget, run)
            async with asyncio.timeout(budget):
                snapshot = await graph.aget_state(config)
                graph_input: workflow.State | Command[str] | None
                if not snapshot.created_at:
                    graph_input = workflow.initial_state(run, ledger)
                elif snapshot.interrupts:
                    approval_id = UUID(snapshot.interrupts[0].value["approval_id"])
                    decision = await run_in_threadpool(self.decision, run, approval_id)
                    if decision is None:
                        await run_in_threadpool(
                            self.finish, run, "waiting_approval", approval_id=approval_id
                        )
                        return
                    graph_input = Command(resume=decision)
                else:
                    graph_input = None
                if graph_input is not None or snapshot.next:
                    await graph.ainvoke(graph_input, config, durability="sync")
                    snapshot = await graph.aget_state(config)
            if snapshot.interrupts:
                approval_id = UUID(snapshot.interrupts[0].value["approval_id"])
                await run_in_threadpool(
                    self.finish, run, "waiting_approval", approval_id=approval_id
                )
                return
            answer = snapshot.values.get("answer")
            if not isinstance(answer, str) or not answer:
                raise AppError(502, "agent_missing_answer", "Workflow ended without an answer")
            await run_in_threadpool(self.finish, run, "completed", answer=answer)
        except Paused:
            await run_in_threadpool(self.finish, run, "paused")
        except ClaimLost:
            return
        except TimeoutError:
            await run_in_threadpool(self.finish, run, "failed", error="agent_time_budget")
        except AppError as exc:
            await run_in_threadpool(self.finish, run, "failed", error=exc.code)
        except Exception as exc:
            logger.warning("agent_workflow_failure", extra={"error_type": type(exc).__name__})
            await run_in_threadpool(
                self.finish, run, "failed", error="agent_worker_error", recoverable=True
            )

    async def run_once(self, *, target: UUID | None = None) -> bool:
        candidates = [target] if target is not None else await run_in_threadpool(self.candidates)
        for run_id in candidates:
            async with await worker_connection(self.settings) as connection:
                # The same session owns the advisory lock and the checkpoint writer.
                cursor = await connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired",
                    (str(run_id),),
                )
                lock = await cursor.fetchone()
                if not lock or not lock["acquired"]:
                    continue
                run = await run_in_threadpool(self.claim, run_id)
                if run is None:
                    continue
                saver = AsyncPostgresSaver(
                    connection, serde=JsonPlusSerializer(allowed_msgpack_modules=())
                )
                with tracing_context(enabled=False):
                    task = asyncio.create_task(self.execute_graph(run, saver))
                    watcher = asyncio.create_task(self.watch_cancellation(run, task))
                    try:
                        await task
                    except asyncio.CancelledError:
                        if not watcher.done():
                            raise
                    finally:
                        watcher.cancel()
                        await asyncio.gather(watcher, return_exceptions=True)
                return True
        return False


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    engine = create_database_engine(settings)
    try:
        async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
            worker = Worker(settings, create_session_factory(engine), client)
            while True:
                try:
                    if await worker.run_once():
                        continue
                except Exception as exc:
                    logger.warning(
                        "agent_worker_poll_failed", extra={"error_type": type(exc).__name__}
                    )
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await run_in_threadpool(engine.dispose)


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main())
