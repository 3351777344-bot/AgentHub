"""Public contracts never expose storage paths, credentials or vectors."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateKnowledgeBase(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"\S")
    chunk_size: int = Field(default=800, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0)

    @model_validator(mode="after")
    def valid_overlap(self) -> "CreateKnowledgeBase":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("Overlap must be smaller than chunk size")
        return self


class KnowledgeBaseOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    embedding_model: str
    dimensions: int
    chunk_size: int
    chunk_overlap: int
    pipeline_version: str
    created_at: datetime


class DocumentOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    knowledge_base_id: UUID
    filename: str
    file_type: str
    checksum: str
    size_bytes: int
    status: str
    version: int
    chunk_count: int
    error_code: str | None
    created_at: datetime


class JobOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version: int
    status: str
    request_id: str
    error_code: str | None
    total_tokens: int | None
    completed_batches: int
    created_at: datetime
    finished_at: datetime | None


class ChunkOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    position: int
    page_number: int
    char_start: int
    char_end: int
    content: str
