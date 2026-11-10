"""Identity HTTP contract; synchronous routes keep blocking work off the event loop."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from agenthub.core.errors import AppError, ErrorResponse
from agenthub.identity import service
from agenthub.identity.dependencies import AdminPrincipal, CurrentPrincipal, DatabaseSession
from agenthub.identity.models import AuditEvent, User
from agenthub.identity.schemas import (
    AuditOutput,
    ChangePasswordInput,
    CreateUserInput,
    LoginInput,
    TokenOutput,
    UpdateUserInput,
    UserOutput,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["identity"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 503)},
)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.post("/auth/login", response_model=TokenOutput)
def login(
    data: LoginInput, request: Request, response: Response, session: DatabaseSession
) -> TokenOutput:
    response.headers["Cache-Control"] = "no-store"
    return service.login(
        session,
        data.username,
        data.password.get_secret_value(),
        request.app.state.settings.session_ttl_seconds,
        request.state.request_id,
    )


@router.get("/auth/me", response_model=UserOutput)
def me(principal: CurrentPrincipal, response: Response) -> User:
    response.headers["Cache-Control"] = "no-store"
    return principal.user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, session: DatabaseSession, principal: CurrentPrincipal) -> Response:
    service.logout(session, principal, request.state.request_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/password", status_code=204)
def change_password(
    data: ChangePasswordInput,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> Response:
    service.change_password(
        session,
        principal,
        data.current_password.get_secret_value(),
        data.new_password.get_secret_value(),
        request.state.request_id,
    )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/users", response_model=UserOutput, status_code=201)
def create_user(
    data: CreateUserInput, request: Request, session: DatabaseSession, principal: AdminPrincipal
) -> User:
    service.lock_admin_operation(session, principal)
    return service.create_user(session, data, request.state.request_id, principal.user.id)


@router.get("/users", response_model=list[UserOutput])
def list_users(
    session: DatabaseSession, principal: AdminPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[User]:
    return list(
        session.scalars(select(User).order_by(User.created_at, User.id).limit(limit).offset(offset))
    )


@router.get("/users/{user_id}", response_model=UserOutput)
def get_user(user_id: UUID, session: DatabaseSession, principal: CurrentPrincipal) -> User:
    # Check the caller before looking up another user's ID, so members cannot enumerate users.
    if user_id != principal.user.id and principal.user.role_name != "admin":
        raise AppError(403, "forbidden", "Resource permission required")
    user = session.get(User, user_id)
    if user is None:
        raise AppError(404, "user_not_found", "User not found")
    return user


@router.patch("/users/{user_id}", response_model=UserOutput)
def update_user(
    user_id: UUID,
    data: UpdateUserInput,
    request: Request,
    session: DatabaseSession,
    principal: AdminPrincipal,
) -> User:
    return service.update_user(session, principal, user_id, data, request.state.request_id)


@router.get("/audit-events", response_model=list[AuditOutput])
def list_audit_events(
    session: DatabaseSession, principal: AdminPrincipal, limit: Limit = 20, offset: Offset = 0
) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
