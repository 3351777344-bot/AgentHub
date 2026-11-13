"""Versioned evaluation inputs and owner-visible reports."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agenthub.agents.schemas import AgentConfig, ToolRef, checked_text

RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
CaseStatus = Literal["queued", "running", "passed", "failed", "skipped"]
FailureStage = Literal["configuration", "retrieval", "model", "tool", "scoring", "execution"]
Label = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
    AfterValidator(checked_text),
]
Question = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
    AfterValidator(checked_text),
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Case(Input):
    key: Label
    question: Question
    tags: list[Label] = Field(default_factory=list, max_length=8)
    answer_contains: list[ShortText] = Field(default_factory=list, max_length=8)


class RagCase(Case):
    kind: Literal["rag"]
    knowledge_base_id: UUID
    outcome: Literal["answered", "insufficient_evidence"]
    gold_chunk_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_gold(self) -> "RagCase":
        if len(set(self.gold_chunk_ids)) != len(self.gold_chunk_ids):
            raise ValueError("Duplicate reference chunks")
        if self.outcome == "answered" and not (self.gold_chunk_ids and self.answer_contains):
            raise ValueError("Answered cases require reference chunks and answer terms")
        if self.outcome == "insufficient_evidence" and (
            self.gold_chunk_ids or self.answer_contains
        ):
            raise ValueError("Abstention cases cannot contain answer references")
        return self


class AgentCase(Case):
    kind: Literal["agent"]
    expected_tools: list[ToolRef] = Field(max_length=20)
    expected_denials: list[Label] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_expectations(self) -> "AgentCase":
        if not (self.answer_contains or self.expected_tools or self.expected_denials):
            raise ValueError("Agent cases require at least one expected behavior")
        return self


Sample = Annotated[RagCase | AgentCase, Field(discriminator="kind")]


class DatasetInput(Input):
    name: Label
    version: Label
    reviewed: Literal[True]
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=8)
    cases: list[Sample] = Field(min_length=1, max_length=30)

    @field_validator("reviewed", mode="before")
    @classmethod
    def require_human_review(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Confirm human review of the dataset")
        return value

    @model_validator(mode="after")
    def validate_cases(self) -> "DatasetInput":
        if len({case.key for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case keys")
        bases = set(self.knowledge_base_ids)
        if len(bases) != len(self.knowledge_base_ids):
            raise ValueError("Duplicate knowledge bases")
        if any(
            isinstance(case, RagCase) and case.knowledge_base_id not in bases for case in self.cases
        ):
            raise ValueError("RAG cases must reference a declared knowledge base")
        return self


class DatasetSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: Label
    version: Label
    digest: str
    created_at: datetime


class DatasetOutput(DatasetSummary):
    data: DatasetInput
    corpus_digest: str


class RunInput(Input):
    dataset_id: UUID
    configuration_version: Label
    agent_id: UUID | None = None
    top_k: int | None = Field(default=None, ge=1, le=20, strict=True)
    min_similarity: float | None = Field(default=None, ge=0, le=1)
    case_timeout_seconds: int = Field(default=90, ge=1, le=900, strict=True)
    run_timeout_seconds: int = Field(default=300, ge=1, le=1800, strict=True)
    max_completion_tokens: int = Field(default=8192, ge=1, le=65536, strict=True)


class Configuration(BaseModel):
    version: Label
    scoring_version: int
    workflow_version: int
    agent_id: UUID | None
    agent: AgentConfig | None
    settings: dict[str, str | int | float | bool]
    model_target: str
    embedding_target: str
    tool_targets: list[str]
    case_timeout_seconds: int
    run_timeout_seconds: int
    max_completion_tokens: int


class RunOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dataset_id: UUID
    dataset_digest: str
    config_digest: str
    config: Configuration
    status: RunStatus
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ReviewInput(Input):
    passed: bool = Field(strict=True)
    note: ShortText


class Review(ReviewInput):
    reviewed_at: datetime


class TraceStep(BaseModel):
    ordinal: int
    kind: str
    status: str
    error_code: str | None
    tool: ToolRef | None = None
    requested_alias: str | None = None


class Observation(BaseModel):
    answer: str | None = None
    outcome: str | None = None
    latency_ms: int | None = None
    embedding_ms: int | None = None
    search_ms: int | None = None
    model_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    embedding_tokens: int | None = None
    retrieved_chunk_ids: list[UUID] = Field(default_factory=list)
    context_chunk_ids: list[UUID] = Field(default_factory=list)
    cited_chunk_ids: list[UUID] = Field(default_factory=list)
    steps: list[TraceStep] = Field(default_factory=list)


class Score(BaseModel):
    answer_terms_match: bool | None = None
    outcome_match: bool | None = None
    recall_at_k: float | None = None
    citations_correct: int = 0
    citations_total: int = 0
    tool_selection_match: bool | None = None


class ResultOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: Label
    ordinal: int
    status: CaseStatus
    failure_stage: FailureStage | None
    error_code: str | None
    agent_run_id: UUID | None
    observation: Observation | None
    score: Score | None
    review: Review | None


class Usage(BaseModel):
    total: int | None
    reported_cases: int


class Report(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int
    task_success_rate: float
    recall_at_k: float | None
    recall_cases: int
    citation_precision: float | None
    citations_correct: int
    citations_total: int
    tool_selection_rate: float | None
    agent_cases: int
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    latency_cases: int
    prompt_tokens: Usage
    completion_tokens: Usage
    embedding_tokens: Usage
    completion_tokens_reserved: int
    human_reviewed: int
    human_passed: int


class CaseComparison(BaseModel):
    key: Label
    baseline_status: CaseStatus
    candidate_status: CaseStatus
    recall_delta: float | None
    citation_precision_delta: float | None
    latency_delta_ms: int | None
    tool_selection_delta: int | None
    baseline_error: str | None
    candidate_error: str | None


class Comparison(BaseModel):
    baseline: RunOutput
    candidate: RunOutput
    baseline_report: Report
    candidate_report: Report
    regressions: list[Label]
    improvements: list[Label]
    cases: list[CaseComparison]
