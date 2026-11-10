"""Identity operations commit business changes and their audit event atomically."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenthub.core.errors import AppError
from agenthub.identity.models import AuditEvent, LoginSession, Role, User
from agenthub.identity.schemas import CreateUserInput, TokenOutput, UpdateUserInput
from agenthub.identity.security import (
    hash_password,
    new_token,
    password_hasher,
    token_digest,
    verify_password,
)


@dataclass(frozen=True)
class Principal:
    user: User
    session_id: UUID


def audit(
    session: Session,
    action: str,
    request_id: str,
    actor_id: UUID | None = None,
    target_user_id: UUID | None = None,
) -> None:
    session.add(
        AuditEvent(
            action=action, request_id=request_id, actor_id=actor_id, target_user_id=target_user_id
        )
    )


def authenticate(session: Session, token: str) -> Principal:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token, flags=re.ASCII):
        raise AppError(401, "invalid_session", "Authentication required")
    row = session.execute(
        select(User, LoginSession.id)
        .join(LoginSession, LoginSession.user_id == User.id)
        .where(
            LoginSession.token_hash == token_digest(token),
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > func.clock_timestamp(),
            User.is_active.is_(True),
        )
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise AppError(401, "invalid_session", "Authentication required")
    return Principal(row[0], row[1])


def require_admin(principal: Principal) -> None:
    if principal.user.role_name != "admin":
        raise AppError(403, "forbidden", "Administrator permission required")


def lock_admin_operation(session: Session, principal: Principal) -> None:
    # All admin writes use the same row lock. Revalidate after waiting: another admin
    # might have disabled/demoted the actor, preventing two admins removing each other.
    session.execute(select(Role).where(Role.name == "admin").with_for_update()).scalar_one()
    session.refresh(principal.user)
    valid = session.scalar(
        select(LoginSession.id).where(
            LoginSession.id == principal.session_id,
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > func.clock_timestamp(),
        )
    )
    if valid is None or not principal.user.is_active:
        raise AppError(401, "invalid_session", "Authentication required")
    require_admin(principal)


def create_user(
    session: Session, data: CreateUserInput, request_id: str, actor_id: UUID | None
) -> User:
    user = User(
        username=data.username,
        password_hash=hash_password(data.password.get_secret_value()),
        role_name=data.role,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        raise AppError(409, "username_exists", "Username already exists") from None
    audit(session, "user.created", request_id, actor_id, user.id)
    session.commit()
    return user


def bootstrap_admin(session: Session, data: CreateUserInput) -> User:
    session.execute(select(Role).where(Role.name == "admin").with_for_update()).scalar_one()
    if session.scalar(select(User.id).limit(1)) is not None:
        raise AppError(409, "already_initialized", "Identity database is already initialized")
    return create_user(session, data.model_copy(update={"role": "admin"}), "bootstrap", None)


def login(
    session: Session, username: str, password: str, ttl_seconds: int, request_id: str
) -> TokenOutput:
    # Serialize login with password change / disable so an old password cannot issue
    # a session after the password change has revoked previously issued sessions.
    user = session.scalar(select(User).where(User.username == username).with_for_update())
    valid = verify_password(password, user.password_hash if user else None)
    if not valid or user is None or not user.is_active:
        audit(session, "auth.login_failed", request_id)
        session.commit()
        raise AppError(401, "invalid_credentials", "Invalid username or password")
    if password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    token = new_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    session.add(
        LoginSession(
            user_id=user.id, token_hash=token_digest(token), created_at=now, expires_at=expires_at
        )
    )
    audit(session, "auth.login", request_id, user.id, user.id)
    session.commit()
    return TokenOutput(access_token=token, expires_at=expires_at)


def revoke_user_sessions(session: Session, user_id: UUID) -> None:
    session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp())
    )


def logout(session: Session, principal: Principal, request_id: str) -> None:
    session.execute(
        update(LoginSession)
        .where(LoginSession.id == principal.session_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=func.clock_timestamp())
    )
    audit(session, "auth.logout", request_id, principal.user.id, principal.user.id)
    session.commit()


def update_user(
    session: Session,
    principal: Principal,
    user_id: UUID,
    data: UpdateUserInput,
    request_id: str,
) -> User:
    lock_admin_operation(session, principal)
    if user_id == principal.user.id:
        raise AppError(
            409, "self_update_forbidden", "Administrators cannot edit their own status/role"
        )
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise AppError(404, "user_not_found", "User not found")
    if data.role is None and data.is_active is None:
        raise AppError(422, "empty_update", "Provide role or is_active")
    changed = False
    if data.role is not None and data.role != user.role_name:
        user.role_name = data.role
        changed = True
    if data.is_active is not None and data.is_active != user.is_active:
        user.is_active = data.is_active
        changed = True
    if changed:
        revoke_user_sessions(session, user.id)
        audit(session, "user.updated", request_id, principal.user.id, user.id)
    session.commit()
    return user


def change_password(
    session: Session, principal: Principal, current: str, new: str, request_id: str
) -> None:
    user = session.scalar(
        select(User)
        .where(User.id == principal.user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    valid_session = session.scalar(
        select(LoginSession.id).where(
            LoginSession.id == principal.session_id,
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > func.clock_timestamp(),
        )
    )
    if user is None or not user.is_active or valid_session is None:
        raise AppError(401, "invalid_session", "Authentication required")
    if not verify_password(current, user.password_hash):
        raise AppError(401, "invalid_credentials", "Invalid username or password")
    user.password_hash = hash_password(new)
    revoke_user_sessions(session, user.id)
    audit(session, "auth.password_changed", request_id, user.id, user.id)
    session.commit()
