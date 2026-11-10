"""Shared authentication context for all subsequent resource modules."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from agenthub.core.errors import AppError
from agenthub.db.session import get_session
from agenthub.identity import service

DatabaseSession = Annotated[Session, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


def get_principal(
    session: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> service.Principal:
    if credentials is None:
        raise AppError(401, "invalid_session", "Authentication required")
    return service.authenticate(session, credentials.credentials)


CurrentPrincipal = Annotated[service.Principal, Depends(get_principal)]


def get_admin(
    principal: CurrentPrincipal, session: DatabaseSession, request: Request
) -> service.Principal:
    if principal.user.role_name != "admin":
        service.audit(session, "authorization.denied", request.state.request_id, principal.user.id)
        session.commit()
    service.require_admin(principal)
    return principal


AdminPrincipal = Annotated[service.Principal, Depends(get_admin)]
