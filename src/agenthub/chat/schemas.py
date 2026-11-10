from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateConversation(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=200)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class ConversationOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime


class MessageOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    position: int
    role: str
    content: str
    status: Literal["pending", "completed", "failed", "cancelled"]
    error_code: str | None
    created_at: datetime
