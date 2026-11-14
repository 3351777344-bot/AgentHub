"""Execute bounded batches through the production RAG and Agent services."""

import asyncio
import logging
import sys
import time
from contextlib import aclosing
from dataclasses import dataclass
from uuid import UUID

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from agenthub.agents import service as agents
from agenthub.agents.models import AgentRun, AgentStep
from agenthub.agents.schemas import RunInput as AgentRunInput
from agenthub.agents.worker import Worker as AgentWorker
from agenthub.agents.worker import worker_connection
from agenthub.chat.provider import ChatProvider
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.core.logging import configure_logging
from agenthub.core.shutdown import stop_requested
from agenthub.db.session import create_database_engine, create_session_factory
from agenthub.embeddings.provider import EmbeddingProvider
from agenthub.evaluations import scoring, service
from agenthub.evaluations.models import EvaluationDataset, EvaluationResult, EvaluationRun
from agenthub.evaluations.schemas import (
    AgentCase,
    Configuration,
    DatasetInput,
    FailureStage,
    Observation,
    RagCase,
    Sample,
    TraceStep,
)
from agenthub.rag import generation
from agenthub.rag.schemas import AskRequest, SearchOutput
from agenthub.rag.service import TurnResult

logger = logging.getLogger("agenthub.evaluations")


@dataclass
class CaseExecution:
    observation: Observation
    failure_stage: FailureStage | None = None
    error_code: str | None = None
    used_completion_tokens: int | None = None


class Worker:
    def __init__(
        self, settings: Settings, factory: sessionmaker[Session], client: httpx2.AsyncClient
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.client = client

    def claim(self) -> EvaluationRun | None:
        with self.factory.begin() as db:
            # The process holding the batch advisory lock is the only claimant.
            for abandoned in db.scalars(
                select(EvaluationRun).where(EvaluationRun.status == "running").with_for_update()
            ):
                service.finish(db, abandoned, "failed", "evaluation_worker_interrupted")
            run = db.scalar(
                select(EvaluationRun)
                .where(EvaluationRun.status == "queued")
                .order_by(EvaluationRun.created_at, EvaluationRun.id)
                .limit(1)
                .with_for_update()
            )
            if run is not None:
                run.status, run.started_at = "running", agents.db_now(db)
            return run

    def configuration(self, config: Configuration) -> Settings:
        if (
            config.scoring_version != scoring.SCORING_VERSION
            or config.workflow_version != agents.WORKFLOW_VERSION
            or config.model_target
            != agents.configured_model(self.settings, self.settings.model_name)
            or config.embedding_target != service.digest(self.settings.embedding_base_url)
        ):
            raise AppError(
                409, "evaluation_configuration_changed", "Evaluation target is unavailable"
            )
        return self.settings.model_copy(update=config.settings)

    def verify(
        self,
        db: Session,
        run: EvaluationRun,
        data: DatasetInput,
        config: Configuration,
        settings: Settings,
        *,
        lock_corpus: bool = False,
    ) -> str:
        role = service.owner_role(db, run.owner_id)
        dataset = service.owned(db, EvaluationDataset, run.dataset_id, run.owner_id)
        service.verify_corpus(db, dataset, data, settings, lock=lock_corpus)
        if (
            config.agent
            and service.check_agent(db, settings, role, config.agent) != config.tool_targets
        ):
            raise AppError(409, "evaluation_tool_changed", "Evaluation tool definition changed")
        return role

    def load(self, run: EvaluationRun) -> DatasetInput:
        with self.factory() as db:
            dataset = service.owned(db, EvaluationDataset, run.dataset_id, run.owner_id)
            return DatasetInput.model_validate(dataset.data)

    def begin_case(
        self,
        run: EvaluationRun,
        data: DatasetInput,
        case: Sample,
        config: Configuration,
        settings: Settings,
    ) -> tuple[int, UUID | None, float] | None:
        with self.factory.begin() as db:
            current = service.owned(db, EvaluationRun, run.id, run.owner_id, lock=True)
            if current.status != "running":
                return None
            time_left = (service.deadline(run, config) - agents.db_now(db)).total_seconds()
            if time_left <= 0:
                service.finish(db, current, "failed", "evaluation_run_timeout")
                return None
            cap = settings.model_max_tokens
            if isinstance(case, AgentCase):
                assert config.agent is not None
                cap = config.agent.max_completion_tokens
            if current.reserved_tokens + cap > config.max_completion_tokens:
                service.finish(db, current, "failed", "evaluation_token_budget")
                return None
            role = self.verify(db, run, data, config, settings)
            result = db.get(EvaluationResult, (run.id, case.key))
            assert result is not None and result.status == "queued"
            result.status = "running"
            current.reserved_tokens += cap
            if isinstance(case, AgentCase):
                assert config.agent_id is not None and config.agent is not None
                agent = agents.owned_agent(db, config.agent_id, run.owner_id)
                child = agents.start_run(
                    db,
                    agent,
                    run.owner_id,
                    role,
                    settings,
                    AgentRunInput(prompt=case.question),
                    config_snapshot=config.agent,
                    commit=False,
                    request_id=run.request_id,
                )
                result.agent_run_id = child.id
            time_left = (service.deadline(run, config) - agents.db_now(db)).total_seconds()
            return cap, result.agent_run_id, time_left

    def save_case(
        self,
        run: EvaluationRun,
        data: DatasetInput,
        case: Sample,
        config: Configuration,
        settings: Settings,
        cap: int,
        execution: CaseExecution,
    ) -> None:
        with self.factory.begin() as db:
            current = service.owned(db, EvaluationRun, run.id, run.owner_id, lock=True)
            if current.status != "running":
                return
            row = db.get(EvaluationResult, (run.id, case.key))
            assert row is not None and row.status == "running"
            service.cancel_child(db, row)
            row.observation = execution.observation.model_dump(mode="json")
            try:
                self.verify(db, run, data, config, settings, lock_corpus=True)
            except AppError as exc:
                service.finish(db, current, "failed", exc.code, stage="configuration")
                return
            stage, code = execution.failure_stage, execution.error_code
            score, mismatch = scoring.score_case(
                case, execution.observation, execution_ok=code is None
            )
            if mismatch:
                stage, code = mismatch, "evaluation_expectation_failed"
            used = execution.used_completion_tokens
            if used is not None:
                current.reserved_tokens += used - cap
                if used > cap:
                    stage, code = "model", "evaluation_completion_limit"
            row.status = "failed" if code else "passed"
            row.failure_stage, row.error_code = stage, code
            row.score = score.model_dump(mode="json")
            if current.reserved_tokens > config.max_completion_tokens:
                service.finish(db, current, "failed", "evaluation_token_budget")
            elif agents.db_now(db) >= service.deadline(run, config) or (
                execution.error_code == "evaluation_run_timeout"
            ):
                service.finish(db, current, "failed", "evaluation_run_timeout")

    def finish(
        self, run: EvaluationRun, code: str | None = None, *, configuration: bool = False
    ) -> None:
        with self.factory.begin() as db:
            current = service.owned(db, EvaluationRun, run.id, run.owner_id, lock=True)
            if current.status == "running":
                service.finish(
                    db,
                    current,
                    "failed" if code else "completed",
                    code,
                    stage="configuration" if configuration else "execution",
                )

    def is_running(self, run: EvaluationRun) -> bool:
        with self.factory() as db:
            return (
                db.scalar(select(EvaluationRun.status).where(EvaluationRun.id == run.id))
                == "running"
            )

    async def watch(self, run: EvaluationRun, task: asyncio.Task[None]) -> None:
        while not task.done():
            await asyncio.sleep(0.25)
            if not await run_in_threadpool(self.is_running, run):
                task.cancel()
                return

    async def execute_rag(
        self, run: EvaluationRun, case: RagCase, settings: Settings, result: TurnResult
    ) -> None:
        async with aclosing(
            generation.generate(
                self.factory,
                ChatProvider(settings, self.client),
                EmbeddingProvider(settings, self.client),
                settings,
                run.owner_id,
                AskRequest(knowledge_base_id=case.knowledge_base_id, question=case.question),
                result,
                verify=True,
            )
        ) as items:
            async for _ in items:
                pass

    @staticmethod
    def rag_observation(result: TurnResult) -> Observation:
        return Observation(
            answer=result.content or None,
            outcome=result.outcome,
            embedding_ms=result.retrieval.embedding_ms,
            search_ms=result.retrieval.search_ms,
            model_ms=result.model_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            embedding_tokens=result.retrieval.embedding_tokens,
            retrieved_chunk_ids=result.retrieved,
            context_chunk_ids=[item.source.chunk_id for item in result.evidence],
            cited_chunk_ids=[item.source.chunk_id for item in result.citations],
        )

    def observe_agent(self, child_id: UUID) -> CaseExecution:
        with self.factory() as db:
            child = db.get(AgentRun, child_id)
            assert child is not None
            steps = list(
                db.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id == child_id)
                    .order_by(AgentStep.ordinal)
                )
            )
            embedding_usage = []
            traces = []
            for step in steps:
                tool = (step.input or {}).get("tool")
                traces.append(
                    TraceStep(
                        ordinal=step.ordinal,
                        kind=step.kind,
                        status=step.status,
                        error_code=step.error_code,
                        tool=tool,
                        requested_alias=(step.input or {}).get("requested_alias"),
                    )
                )
                if tool and tool["name"] == "knowledge_search" and step.output is not None:
                    stats = SearchOutput.model_validate(step.output).retrieval
                    if stats.embedding_tokens is not None:
                        embedding_usage.append(stats.embedding_tokens)
            model_observed = any(
                step.kind == "model"
                and (step.output is not None or step.error_code == "agent_token_budget")
                for step in steps
            )
            obs = Observation(
                answer=child.answer,
                outcome=child.status,
                steps=traces,
                prompt_tokens=child.prompt_tokens if model_observed else None,
                completion_tokens=child.completion_tokens if model_observed else None,
                embedding_tokens=sum(embedding_usage) if embedding_usage else None,
            )
            if child.status == "completed":
                return CaseExecution(obs, used_completion_tokens=child.completion_tokens)
            code = child.error_code or "evaluation_agent_incomplete"
            last = next(
                (
                    step
                    for step in reversed(steps)
                    if step.status in {"failed", "rejected", "uncertain"}
                ),
                None,
            )
            stage: FailureStage = "execution"
            if last and last.kind == "tool":
                stage = "tool"
            elif (last and last.kind == "model") or code.startswith(("model_", "agent_model_")):
                stage = "model"
            elif child.status == "waiting_approval":
                stage = "tool"
            return CaseExecution(obs, stage, code)

    async def execute_case(
        self,
        run: EvaluationRun,
        case: Sample,
        settings: Settings,
        child_id: UUID | None,
        time_limit: float,
        timeout_code: str,
    ) -> CaseExecution:
        result = TurnResult()
        stage: FailureStage | None = None
        code: str | None = None
        started = time.monotonic()
        try:
            async with asyncio.timeout(time_limit):
                if isinstance(case, RagCase):
                    await self.execute_rag(run, case, settings, result)
                else:
                    assert child_id is not None
                    await AgentWorker(settings, self.factory, self.client).run_once(target=child_id)
        except TimeoutError:
            stage, code = (result.stage if isinstance(case, RagCase) else "execution"), timeout_code
        except AppError as exc:
            stage, code = (result.stage if isinstance(case, RagCase) else "execution"), exc.code
        if child_id is not None:
            execution = await run_in_threadpool(self.observe_agent, child_id)
            if code:
                execution.failure_stage, execution.error_code = stage, code
                execution.used_completion_tokens = None
        else:
            used = result.completion_tokens
            if code is None and result.model_ms is None:
                used = 0
            execution = CaseExecution(self.rag_observation(result), stage, code, used)
        execution.observation.latency_ms = int((time.monotonic() - started) * 1000)
        return execution

    async def execute(self, run: EvaluationRun) -> None:
        try:
            config = Configuration.model_validate(run.config)
            settings = self.configuration(config)
            data = await run_in_threadpool(self.load, run)
            for case in data.cases:
                ticket = await run_in_threadpool(self.begin_case, run, data, case, config, settings)
                if ticket is None:
                    return
                cap, child_id, time_left = ticket
                timeout_code = "evaluation_case_timeout"
                if time_left <= config.case_timeout_seconds:
                    timeout_code = "evaluation_run_timeout"
                execution = await self.execute_case(
                    run,
                    case,
                    settings,
                    child_id,
                    min(config.case_timeout_seconds, time_left),
                    timeout_code,
                )
                await run_in_threadpool(
                    self.save_case, run, data, case, config, settings, cap, execution
                )
            await run_in_threadpool(self.finish, run)
        except AppError as exc:
            await run_in_threadpool(self.finish, run, exc.code, configuration=True)
        except Exception as exc:
            logger.warning(
                "evaluation_execution_failed",
                extra={
                    "error_type": type(exc).__name__,
                    "evaluation_run_id": str(run.id),
                    "request_id": run.request_id or "-",
                },
            )
            await run_in_threadpool(self.finish, run, "evaluation_worker_error")

    async def run_once(self) -> bool:
        async with await worker_connection(self.settings) as connection:
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock("
                "hashtextextended('agenthub.evaluation.batch.v1', 0)) AS acquired"
            )
            lock = await cursor.fetchone()
            if not lock or not lock["acquired"]:
                return False
            run = await run_in_threadpool(self.claim)
            if run is None:
                return False
            fields = {"evaluation_run_id": str(run.id), "request_id": run.request_id or "-"}
            logger.info("evaluation_execution_started", extra=fields)
            task = asyncio.create_task(self.execute(run))
            watcher = asyncio.create_task(self.watch(run, task))
            try:
                await task
            except asyncio.CancelledError:
                if not watcher.done():
                    raise
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            logger.info("evaluation_execution_stopped", extra=fields)
            return True


async def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    try:
        with stop_requested() as stop:
            async with httpx2.AsyncClient(follow_redirects=False, trust_env=False) as client:
                worker = Worker(settings, create_session_factory(engine), client)
                while not stop.is_set():
                    try:
                        if await worker.run_once():
                            continue
                    except Exception as exc:
                        logger.warning(
                            "evaluation_worker_poll_failed",
                            extra={"error_type": type(exc).__name__},
                        )
                    await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await run_in_threadpool(engine.dispose)
        logger.info("evaluation_worker_stopped")


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main())
