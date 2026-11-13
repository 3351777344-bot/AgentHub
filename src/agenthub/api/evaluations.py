"""Owner-scoped evaluation dataset, execution, review and report endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.evaluations import service
from agenthub.evaluations.models import EvaluationDataset, EvaluationResult, EvaluationRun
from agenthub.evaluations.schemas import (
    Comparison,
    DatasetInput,
    DatasetOutput,
    DatasetSummary,
    Label,
    Report,
    ResultOutput,
    ReviewInput,
    RunInput,
    RunOutput,
)
from agenthub.identity.dependencies import CurrentPrincipal, DatabaseSession

router = APIRouter(
    prefix="/api/v1/evaluations",
    tags=["evaluations"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 413, 422, 503)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post("/datasets", response_model=DatasetOutput, status_code=201)
def create_dataset(
    data: DatasetInput, request: Request, session: DatabaseSession, principal: CurrentPrincipal
) -> EvaluationDataset:
    return service.create_dataset(session, data, principal.user.id, request.app.state.settings)


@router.get("/datasets", response_model=list[DatasetSummary])
def list_datasets(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[EvaluationDataset]:
    return list(
        session.scalars(
            select(EvaluationDataset)
            .where(EvaluationDataset.owner_id == principal.user.id)
            .order_by(EvaluationDataset.created_at.desc(), EvaluationDataset.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/datasets/{dataset_id}", response_model=DatasetOutput)
def get_dataset(
    dataset_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> EvaluationDataset:
    return service.owned(session, EvaluationDataset, dataset_id, principal.user.id)


@router.post("/runs", response_model=RunOutput, status_code=202)
def create_run(
    data: RunInput, request: Request, session: DatabaseSession, principal: CurrentPrincipal
) -> EvaluationRun:
    return service.start_run(session, data, principal.user.id, request.app.state.settings)


@router.get("/runs", response_model=list[RunOutput])
def list_runs(
    session: DatabaseSession, principal: CurrentPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[EvaluationRun]:
    return list(
        session.scalars(
            select(EvaluationRun)
            .where(EvaluationRun.owner_id == principal.user.id)
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/runs/{run_id}", response_model=RunOutput)
def get_run(run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> EvaluationRun:
    return service.owned(session, EvaluationRun, run_id, principal.user.id)


@router.post("/runs/{run_id}/cancel", response_model=RunOutput)
def cancel(run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> EvaluationRun:
    run = service.owned(session, EvaluationRun, run_id, principal.user.id, lock=True)
    if run.status == "cancelled":
        return run
    if run.status not in service.ACTIVE:
        raise AppError(409, "evaluation_run_finished", "Evaluation run has already finished")
    service.finish(session, run, "cancelled")
    session.commit()
    return run


@router.get("/runs/{run_id}/results", response_model=list[ResultOutput])
def list_results(
    run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> list[EvaluationResult]:
    service.owned(session, EvaluationRun, run_id, principal.user.id)
    return service.results(session, run_id)


@router.put("/runs/{run_id}/results/{key}/review", response_model=ResultOutput)
def review(
    run_id: UUID,
    key: Label,
    data: ReviewInput,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> EvaluationResult:
    run = service.owned(session, EvaluationRun, run_id, principal.user.id, lock=True)
    return service.review(session, run, key, data)


@router.get("/runs/{run_id}/report", response_model=Report)
def get_report(run_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> Report:
    return service.report(service.owned(session, EvaluationRun, run_id, principal.user.id))


@router.get("/compare", response_model=Comparison)
def compare(
    baseline_id: UUID, candidate_id: UUID, session: DatabaseSession, principal: CurrentPrincipal
) -> Comparison:
    baseline = service.owned(session, EvaluationRun, baseline_id, principal.user.id)
    candidate = service.owned(session, EvaluationRun, candidate_id, principal.user.id)
    return service.compare(session, baseline, candidate)
