"""Public tool contracts and configuration inputs."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ServiceKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,39}$")]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
RemoteName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
Version = Annotated[int, Field(ge=1, le=1_000_000)]
Role = Literal["admin", "member"]
Effect = Literal["read_only", "write", "high"]
Status = Literal["started", "completed", "rejected", "failed", "cancelled"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddInput(Input):
    left: int = Field(ge=-1_000_000, le=1_000_000, strict=True)
    right: int = Field(ge=-1_000_000, le=1_000_000, strict=True)


class IntegerTotal(BaseModel):
    total: int


class ServiceCreate(Input):
    key: ServiceKey


class EnabledUpdate(Input):
    enabled: bool = Field(strict=True)


class ServiceOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: ServiceKey
    transport: Literal["unix_http", "https"]
    enabled: bool


class DiscoveredTool(BaseModel):
    name: RemoteName
    remote_description: str | None = Field(max_length=1000)
    read_only_hint: bool | None
    destructive_hint: bool | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    definition_digest: str


class ToolRegistration(Input):
    name: ToolName
    version: Version
    service_key: ServiceKey
    remote_name: RemoteName
    definition_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    required_role: Role = "admin"
    effect: Effect
    timeout_seconds: int = Field(default=10, ge=1, le=60, strict=True)

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str) -> str:
        value.encode("utf-8")
        if "\x00" in value:
            raise ValueError("Description contains a null character")
        return value


class ToolDescription(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: ToolName
    version: Version
    source: Literal["builtin", "mcp"]
    description: str
    required_role: Role
    effect: Effect
    timeout_seconds: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class RegisteredTool(ToolDescription):
    service_key: ServiceKey
    remote_name: RemoteName
    definition_digest: str
    enabled: bool


class CallInput(Input):
    arguments: dict[str, Any]


class CallOutput(BaseModel):
    call_id: UUID
    result: dict[str, Any]


class CallAudit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID
    request_id: str
    tool_name: str | None
    tool_version: int | None
    service_key: str | None
    status: Status
    error_code: str | None
    duration_ms: int | None
    created_at: datetime
    finished_at: datetime | None
