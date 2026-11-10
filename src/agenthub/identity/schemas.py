"""Explicit public responses never serialize password or token hashes."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)


def normalize_username(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


Username = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9_][a-z0-9_.-]{2,63}$"),
    BeforeValidator(normalize_username),
]
RoleName = Literal["admin", "member"]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LoginInput(InputModel):
    username: Username
    password: SecretStr = Field(min_length=1, max_length=128)


class CreateUserInput(InputModel):
    username: Username
    password: SecretStr = Field(min_length=12, max_length=128)
    role: RoleName = "member"

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Password cannot be blank")
        return value


class UpdateUserInput(InputModel):
    role: RoleName | None = None
    is_active: bool | None = Field(default=None, strict=True)


class ChangePasswordInput(InputModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def reject_blank_password(cls, value: SecretStr) -> SecretStr:
        return CreateUserInput.reject_blank_password(value)


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role_name: RoleName
    is_active: bool
    created_at: datetime


class TokenOutput(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class AuditOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None
    target_user_id: UUID | None
    action: str
    request_id: str
    created_at: datetime
