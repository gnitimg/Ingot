from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(default="", max_length=2048)
    model: str = Field(min_length=1, max_length=300)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("提供商地址必须是有效的 http(s) URL")
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("模型名称不能为空")
        return value


class EmbeddingSettingsUpdate(ProviderSettingsUpdate):
    batch_size: int = Field(ge=1, le=128)
    timeout: float = Field(ge=5.0, le=300.0)

    @model_validator(mode="after")
    def require_base_url(self) -> "EmbeddingSettingsUpdate":
        if not self.base_url:
            raise ValueError("Embedding 提供商地址不能为空")
        return self


class ChatSettingsUpdate(ProviderSettingsUpdate):
    use_embedding_provider: bool = False
    timeout: float = Field(ge=10.0, le=600.0)
    temperature: float = Field(ge=0.0, le=2.0)
    max_tokens: int = Field(ge=128, le=32768)


class OCRSettingsUpdate(ProviderSettingsUpdate):
    enabled: bool = True
    use_embedding_provider: bool = False
    timeout: float = Field(ge=10.0, le=600.0)
    concurrency: int = Field(ge=1, le=8)
    min_text_chars: int = Field(ge=0, le=2000)
    max_pages: int = Field(ge=1, le=2000)
    render_dpi: int = Field(ge=72, le=300)


class RerankSettingsUpdate(ProviderSettingsUpdate):
    enabled: bool = True
    use_embedding_provider: bool = False
    candidates: int = Field(ge=2, le=100)
    timeout: float = Field(ge=5.0, le=300.0)


class ChunkingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_size: int = Field(ge=200, le=8000)
    chunk_overlap: int = Field(ge=0, le=2000)
    default_top_k: int = Field(ge=1, le=30)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingSettingsUpdate":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("重叠字符数必须小于单块字符数")
        return self


class GraphSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(ge=1, le=10)
    max_chunks: int = Field(ge=0)
    chunk_timeout: float = Field(ge=15, le=1800)
    build_timeout: float = Field(ge=60, le=86400)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    embedding: EmbeddingSettingsUpdate
    chat: ChatSettingsUpdate
    ocr: OCRSettingsUpdate
    rerank: RerankSettingsUpdate
    chunking: ChunkingSettingsUpdate
    graph: GraphSettingsUpdate


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["vector", "graph_local", "graph_global", "hybrid"] = "hybrid"
    top_k: int = Field(default=6, ge=1, le=30)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(SearchRequest):
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

