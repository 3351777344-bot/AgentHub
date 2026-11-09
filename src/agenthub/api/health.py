"""Liveness is process health; readiness requires real dependencies."""

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from agenthub.core.dependencies import CheckStatus, ReadinessChecker
from agenthub.core.errors import ErrorResponse, error_response

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    checks: dict[str, CheckStatus]


def get_readiness_checker(request: Request) -> ReadinessChecker:
    return cast(ReadinessChecker, request.app.state.dependencies)


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse, "description": "Dependencies are unavailable"}},
)
async def readiness(
    request: Request,
    checker: Annotated[ReadinessChecker, Depends(get_readiness_checker)],
) -> ReadinessResponse | JSONResponse:
    checks = await checker.check()
    if not checks or any(status != "ok" for status in checks.values()):
        return error_response(
            503,
            "not_ready",
            "Required dependencies are unavailable",
            request.state.request_id,
            details=checks,
        )
    return ReadinessResponse(checks=checks)
