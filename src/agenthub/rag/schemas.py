from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from agenthub.chat.schemas import MessageOutput

Outcome = Literal["answered", "insufficient_evidence"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)
    ]
    top_k: int | None = Field(default=None, ge=1, le=20)
    min_similarity: float | None = Field(default=None, ge=0, le=1)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value.encode("utf-8")
        if "\x00" in value:
            raise ValueError("Question contains a null character")
        return value


class AskRequest(SearchRequest):
    knowledge_base_id: UUID


class Source(BaseModel):
    document_id: UUID
    document_version: int
    filename: str
    chunk_id: UUID
    position: int
    page_number: int
    char_start: int
    char_end: int
    similarity: float


class SearchHit(BaseModel):
    source: Source
    content: str


class RetrievalStats(BaseModel):
    embedding_ms: int | None = None
    embedding_tokens: int | None = None
    search_ms: int | None = None
    retrieved_count: int | None = None
    context_count: int | None = None


class SearchOutput(BaseModel):
    hits: list[SearchHit]
    retrieval: RetrievalStats


class Citation(BaseModel):
    label: str
    source: Source
    available: bool = True


class RagOutput(BaseModel):
    message: MessageOutput
    knowledge_base_id: UUID
    outcome: Outcome | None
    citations: list[Citation]
    retrieval: RetrievalStats
