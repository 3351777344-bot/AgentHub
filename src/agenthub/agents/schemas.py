"""Private Agent configuration, run control, and owner-visible records."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from agenthub.tools.schemas import Input, ToolName, Version

RunStatus = Literal[
    "queued", "running", "paused", "waiting_approval", "completed", "failed", "cancelled"
]
Decision = Literal["approve", "reject"]
CommandName = Literal["pause", "resume", "cancel"]


def checked_text(value: str) -> str:
    value.encode("utf-8")
    if "\x00" in value:
        raise ValueError("Text contains a null character")
    return value


class ToolRef(Input):
    name: ToolName
    version: Version


class AgentConfig(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    system_prompt: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
    ]
    model_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=8)
    tools: list[ToolRef] = Field(default_factory=list, max_length=8)
    max_steps: int = Field(default=12, ge=1, le=40, strict=True)
    max_seconds: int = Field(default=180, ge=1, le=900, strict=True)
    max_completion_tokens: int = Field(default=2048, ge=1, le=32768, strict=True)

    @model_validator(mode="after")
    def validate_config(self) -> "AgentConfig":
        for value in (self.name, self.system_prompt, self.model_name):
            checked_text(value)
        if len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids):
            raise ValueError("Duplicate knowledge bases")
        if len({(tool.name, tool.version) for tool in self.tools}) != len(self.tools):
            raise ValueError("Duplicate tool versions")
        return self


class AgentOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    config: AgentConfig
    created_at: datetime


class RunInput(Input):
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def validate_prompt(self) -> "RunInput":
        checked_text(self.prompt)
        return self


class RunOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_id: UUID
    conversation_id: UUID | None
    workflow_version: int
    status: RunStatus
    prompt: str
    approval_id: UUID | None
    pause_requested: bool
    recoverable: bool
    prompt_tokens: int
    completion_tokens: int
    answer: str | None
    error_code: str | None
    event_seq: int
    created_at: datetime
    updated_at: datetime


class StepOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    kind: str
    status: str
    error_code: str | None
    tool_call_id: UUID | None
    created_at: datetime
    finished_at: datetime | None


class EventOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    kind: str
    data: dict[str, Any]
    created_at: datetime


class ApprovalInput(Input):
    digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    decision: Decision


class ApprovalOutput(BaseModel):
    id: UUID
    run_id: UUID
    step_id: UUID
    digest: str
    tool: ToolRef
    effect: Literal["write", "high"]
    arguments: dict[str, Any]
    decision: Decision | None
    decided_at: datetime | None
