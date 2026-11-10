"""Stable public errors without echoing exception text or invalid input."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AppError(Exception):
    """Public error contract containing no raw driver or credential information."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    *,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, request_id=request_id, details=details or {})
    )
    return JSONResponse(body.model_dump(), status_code=status_code, headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    async def handle_database_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(
            503,
            "database_unavailable",
            "Database temporarily unavailable",
            request.state.request_id,
        )

    for exception in (OperationalError, InterfaceError, DatabaseTimeoutError):
        app.add_exception_handler(exception, handle_database_error)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            exc.status_code,
            exc.code,
            exc.message,
            request.state.request_id,
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        # HTTPException.detail can contain upstream secrets, so expose only a standard reason.
        try:
            message = HTTPStatus(exc.status_code).phrase
        except ValueError:
            message = "Request failed"
        return error_response(
            exc.status_code,
            f"http_{exc.status_code}",
            message,
            request.state.request_id,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Do not return .errors(): input and validator context may contain credentials.
        return error_response(
            422, "validation_error", "Request validation failed", request.state.request_id
        )
