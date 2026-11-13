"""Owner-scoped evaluation lifecycle and immutable corpus/configuration snapshots."""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenthub.agents import service as agents
from agenthub.agents.models import AgentRun
from agenthub.agents.schemas import AgentConfig
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.evaluations import scoring
from agenthub.evaluations.models import EvaluationDataset, EvaluationResult, EvaluationRun
from agenthub.evaluations.schemas import (
    AgentCase,
    CaseComparison,
    Comparison,
    Configuration,
    DatasetInput,
    FailureStage,
    Observation,
    RagCase,
    Report,
    Review,
    ReviewInput,
    RunInput,
    RunOutput,
    Score,
)
from agenthub.identity.models import User
from agenthub.knowledge.models import Document, DocumentChunk
from agenthub.knowledge.service import compatible, owned_kb
from agenthub.rag.retrieval import available_chunks, eligible_chunks

ACTIVE = {"queued", "running"}
SETTINGS_FIELDS = {
    "model_name",
    "model_max_tokens",
    "model_timeout_seconds",
    "model_context_characters",
    "model_thinking_mode",
    "embedding_model",
    "embedding_dimensions",
    "embedding_timeout_seconds",
    "embedding_batch_size",
    "embedding_max_characters",
    "rag_top_k",
    "rag_min_similarity",
    "rag_context_characters",
}


def digest(data: Any) -> str:
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def owned[Resource: (EvaluationDataset, EvaluationRun)](
    session: Session, model: type[Resource], identifier: UUID, owner: UUID, *, lock: bool = False
) -> Resource:
    query = select(model).where(model.id == identifier, model.owner_id == owner)
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError(404, "evaluation_not_found", "Evaluation resource not found")
    return row


def owner_role(session: Session, owner: UUID) -> str:
    role = session.scalar(select(User.role_name).where(User.id == owner, User.is_active))
    if role is None:
        raise AppError(403, "evaluation_owner_disabled", "Evaluation owner is disabled")
    return role


def corpus_digest(
    session: Session, data: DatasetInput, owner: UUID, settings: Settings, *, lock: bool = False
) -> str:
    manifest = hashlib.sha256()
    for kb_id in sorted(data.knowledge_base_ids):
        kb = owned_kb(session, kb_id, owner, lock=lock)
        compatible(kb, settings)
        manifest.update(
            digest([str(kb_id), kb.pipeline_version, kb.chunk_size, kb.chunk_overlap]).encode()
        )
        query = (
            eligible_chunks(kb_id, owner)
            .add_columns(Document.id, Document.version, Document.checksum, DocumentChunk.position)
            .order_by(DocumentChunk.id)
        )
        for chunk_id, document_id, version, checksum, position in session.execute(query):
            manifest.update(
                digest([str(chunk_id), str(document_id), version, checksum, position]).encode()
            )
    return manifest.hexdigest()


def create_dataset(
    session: Session, data: DatasetInput, owner: UUID, settings: Settings
) -> EvaluationDataset:
    snapshot = corpus_digest(session, data, owner, settings, lock=True)
    for case in data.cases:
        if isinstance(case, RagCase):
            if set(case.gold_chunk_ids) != available_chunks(
                session, case.knowledge_base_id, owner, case.gold_chunk_ids
            ):
                raise AppError(
                    422, "evaluation_reference_invalid", "Reference chunk is unavailable"
                )
    payload = data.model_dump(mode="json")
    row = EvaluationDataset(
        owner_id=owner,
        name=data.name,
        version=data.version,
        data=payload,
        corpus_digest=snapshot,
        digest=digest([payload, snapshot]),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if (
            getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            != "uq_evaluation_dataset_version"
        ):
            raise
        raise AppError(409, "evaluation_dataset_exists", "Dataset version already exists") from None
    return row


def verify_corpus(
    session: Session,
    dataset: EvaluationDataset,
    data: DatasetInput,
    settings: Settings,
    *,
    lock: bool = False,
) -> None:
    try:
        current = corpus_digest(session, data, dataset.owner_id, settings, lock=lock)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        current = None
    if current != dataset.corpus_digest:
        raise AppError(
            409, "evaluation_corpus_changed", "Create a new dataset version for this corpus"
        )


def check_agent(session: Session, settings: Settings, role: str, config: AgentConfig) -> list[str]:
    plans = agents.tools_for(session, settings, role, config)
    if any(plan.definition.effect != "read_only" for plan in plans):
        raise AppError(422, "evaluation_tool_effect", "Evaluation requires read-only tools")
    return [plan.fingerprint() for plan in plans]


def start_run(
    session: Session, request: RunInput, owner: UUID, settings: Settings
) -> EvaluationRun:
    dataset = owned(session, EvaluationDataset, request.dataset_id, owner)
    data = DatasetInput.model_validate(dataset.data)
    verify_corpus(session, dataset, data, settings)
    values = settings.model_dump(include=SETTINGS_FIELDS)
    if request.top_k is not None:
        values["rag_top_k"] = request.top_k
    if request.min_similarity is not None:
        values["rag_min_similarity"] = request.min_similarity
    agent_config = None
    tool_targets: list[str] = []
    if any(isinstance(case, AgentCase) for case in data.cases) != (request.agent_id is not None):
        raise AppError(
            422, "evaluation_agent_required", "Set agent_id for datasets with Agent cases"
        )
    if request.agent_id is not None:
        agent = agents.owned_agent(session, request.agent_id, owner)
        agent_config = AgentConfig.model_validate(agent.config)
        if not set(agent_config.knowledge_base_ids) <= set(data.knowledge_base_ids):
            raise AppError(
                422, "evaluation_agent_corpus", "Agent knowledge bases must be in the dataset"
            )
        role = owner_role(session, owner)
        agents.validate_config(session, settings, owner, role, agent_config)
        tool_targets = check_agent(session, settings, role, agent_config)
    config = Configuration(
        version=request.configuration_version,
        scoring_version=scoring.SCORING_VERSION,
        workflow_version=agents.WORKFLOW_VERSION,
        agent_id=request.agent_id,
        agent=agent_config,
        settings=values,
        model_target=agents.configured_model(settings, settings.model_name),
        embedding_target=digest(settings.embedding_base_url),
        tool_targets=tool_targets,
        case_timeout_seconds=request.case_timeout_seconds,
        run_timeout_seconds=request.run_timeout_seconds,
        max_completion_tokens=request.max_completion_tokens,
    )
    payload = config.model_dump(mode="json")
    run = EvaluationRun(
        owner_id=owner,
        dataset_id=dataset.id,
        dataset_digest=dataset.digest,
        config=payload,
        config_digest=digest(payload),
    )
    session.add(run)
    session.flush()
    session.add_all(
        EvaluationResult(run_id=run.id, key=case.key, ordinal=index)
        for index, case in enumerate(data.cases, 1)
    )
    session.commit()
    return run


def results(session: Session, run_id: UUID) -> list[EvaluationResult]:
    return list(
        session.scalars(
            select(EvaluationResult)
            .where(EvaluationResult.run_id == run_id)
            .order_by(EvaluationResult.ordinal)
        )
    )


def deadline(run: EvaluationRun, config: Configuration) -> datetime:
    assert run.started_at is not None
    return run.started_at + timedelta(seconds=config.run_timeout_seconds)


def cancel_child(session: Session, row: EvaluationResult) -> None:
    if row.agent_run_id is None:
        return
    child = session.get(AgentRun, row.agent_run_id, with_for_update=True)
    if child is not None and child.status not in agents.TERMINAL:
        agents.mark_started_uncertain(session, child)
        agents.transition(session, child, "cancelled")


def finish(
    session: Session,
    run: EvaluationRun,
    status: str,
    code: str | None = None,
    *,
    stage: FailureStage = "execution",
) -> None:
    rows = results(session, run.id)
    for row in rows:
        if row.status == "running":
            cancel_child(session, row)
            row.status = "skipped" if status == "cancelled" else "failed"
            row.failure_stage = stage
            row.error_code = code or "evaluation_cancelled"
        elif row.status == "queued":
            row.status = "skipped"
            row.failure_stage = stage
            row.error_code = code or "evaluation_cancelled"
    run.status, run.error_code = status, code
    run.finished_at = agents.db_now(session)
    run.report = scoring.summarize(rows, run.reserved_tokens).model_dump(mode="json")


def report(run: EvaluationRun) -> Report:
    if run.report is None:
        raise AppError(409, "evaluation_run_in_progress", "Evaluation report is not ready")
    return Report.model_validate(run.report)


def review(session: Session, run: EvaluationRun, key: str, data: ReviewInput) -> EvaluationResult:
    report(run)
    row = session.get(EvaluationResult, (run.id, key))
    if row is None:
        raise AppError(404, "evaluation_case_not_found", "Evaluation case not found")
    if row.observation is None:
        raise AppError(409, "evaluation_case_not_reviewable", "Case has no execution observation")
    row.review = Review(**data.model_dump(), reviewed_at=agents.db_now(session)).model_dump(
        mode="json"
    )
    run.report = scoring.summarize(results(session, run.id), run.reserved_tokens).model_dump(
        mode="json"
    )
    session.commit()
    return row


def delta(before: float | None, after: float | None) -> float | None:
    return after - before if before is not None and after is not None else None


def compare_case(before: EvaluationResult, after: EvaluationResult) -> CaseComparison:
    left, right = (
        Score.model_validate(item.score) if item.score is not None else Score()
        for item in (before, after)
    )
    old, new = (
        Observation.model_validate(item.observation)
        if item.observation is not None
        else Observation()
        for item in (before, after)
    )
    return CaseComparison(
        key=before.key,
        baseline_status=before.status,
        candidate_status=after.status,
        recall_delta=delta(left.recall_at_k, right.recall_at_k),
        citation_precision_delta=delta(
            left.citations_correct / left.citations_total if left.citations_total else None,
            right.citations_correct / right.citations_total if right.citations_total else None,
        ),
        latency_delta_ms=(new.latency_ms - old.latency_ms)
        if new.latency_ms is not None and old.latency_ms is not None
        else None,
        tool_selection_delta=(int(right.tool_selection_match) - int(left.tool_selection_match))
        if right.tool_selection_match is not None and left.tool_selection_match is not None
        else None,
        baseline_error=before.error_code,
        candidate_error=after.error_code,
    )


def compare(session: Session, baseline: EvaluationRun, candidate: EvaluationRun) -> Comparison:
    if (
        baseline.dataset_id != candidate.dataset_id
        or baseline.dataset_digest != candidate.dataset_digest
        or baseline.config["scoring_version"] != candidate.config["scoring_version"]
    ):
        raise AppError(
            409, "evaluation_comparison_mismatch", "Runs need the same dataset and scoring version"
        )
    baseline_report, candidate_report = report(baseline), report(candidate)
    cases = [
        compare_case(before, after)
        for before, after in zip(
            results(session, baseline.id), results(session, candidate.id), strict=True
        )
    ]
    return Comparison(
        baseline=RunOutput.model_validate(baseline),
        candidate=RunOutput.model_validate(candidate),
        baseline_report=baseline_report,
        candidate_report=candidate_report,
        regressions=[
            case.key
            for case in cases
            if case.baseline_status == "passed" and case.candidate_status != "passed"
        ],
        improvements=[
            case.key
            for case in cases
            if case.baseline_status != "passed" and case.candidate_status == "passed"
        ],
        cases=cases,
    )
