"""Aggregate operational signals without exposing resource identifiers or content."""

from fastapi import APIRouter
from sqlalchemy import func, select

from agenthub.agents.models import AgentRun
from agenthub.evaluations.models import EvaluationRun
from agenthub.identity.dependencies import AdminPrincipal, DatabaseSession
from agenthub.knowledge.models import IngestionJob

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.get("/metrics")
def queue_metrics(session: DatabaseSession, principal: AdminPrincipal) -> dict[str, dict[str, int]]:
    return {
        name: {
            status: count
            for status, count in session.execute(
                select(model.status, func.count()).group_by(model.status)
            )
        }
        for name, model in (
            ("ingestion", IngestionJob),
            ("agents", AgentRun),
            ("evaluations", EvaluationRun),
        )
    }
