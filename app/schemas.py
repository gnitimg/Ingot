from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["vector", "graph_local", "graph_global", "hybrid"] = "hybrid"
    top_k: int = Field(default=6, ge=1, le=30)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(SearchRequest):
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

