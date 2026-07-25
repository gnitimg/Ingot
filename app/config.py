from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


API_KEY_PLACEHOLDERS = {"", "***", "replace-with-your-siliconflow-key", "your-api-key"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Ingot"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("./data")
    max_upload_mb: int = Field(default=50, ge=1, le=1024)
    device_cookie_secret: str = ""

    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_timeout: float = Field(default=90.0, ge=5.0)

    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = "Qwen/Qwen3-8B"
    chat_timeout: float = Field(default=180.0, ge=10.0)
    chat_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    chat_max_tokens: int = Field(default=2048, ge=128, le=32768)

    ocr_enabled: bool = True
    ocr_base_url: str = ""
    ocr_api_key: str = ""
    ocr_model: str = "PaddlePaddle/PaddleOCR-VL-1.5"
    ocr_timeout: float = Field(default=240.0, ge=10.0)
    ocr_concurrency: int = Field(default=2, ge=1, le=8)
    ocr_min_text_chars: int = Field(default=80, ge=0, le=2000)
    ocr_max_pages: int = Field(default=100, ge=1, le=2000)
    ocr_render_dpi: int = Field(default=144, ge=72, le=300)

    rerank_enabled: bool = True
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_candidates: int = Field(default=18, ge=2, le=100)
    rerank_timeout: float = Field(default=60.0, ge=5.0)

    chunk_size: int = Field(default=900, ge=200, le=8000)
    chunk_overlap: int = Field(default=160, ge=0, le=2000)
    default_top_k: int = Field(default=6, ge=1, le=30)
    qa_evidence_count: int = Field(default=6, ge=1, le=30)

    graph_concurrency: int = Field(default=3, ge=1, le=1000)
    graph_max_chunks: int = Field(default=0, ge=0)
    graph_chunk_timeout: float = Field(default=240.0, ge=15.0, le=1800.0)
    graph_build_timeout: float = Field(default=3600.0, ge=60.0, le=86400.0)
    graph_retry_rounds: int = Field(default=2, ge=0, le=5)
    graph_retry_backoff: float = Field(default=2.0, ge=0.1, le=60.0)
    graph_success_threshold: float = Field(default=90.0, ge=1.0, le=100.0)
    graph_llm_entity_matching: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / "ingot.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def effective_chat_base_url(self) -> str:
        return self.chat_base_url.strip() or self.embedding_base_url

    @property
    def effective_chat_api_key(self) -> str:
        return self.chat_api_key.strip() or self.embedding_api_key

    @property
    def effective_ocr_base_url(self) -> str:
        return self.ocr_base_url.strip() or self.embedding_base_url

    @property
    def effective_ocr_api_key(self) -> str:
        return self.ocr_api_key.strip() or self.embedding_api_key

    @property
    def effective_rerank_base_url(self) -> str:
        return self.rerank_base_url.strip() or self.embedding_base_url

    @property
    def effective_rerank_api_key(self) -> str:
        return self.rerank_api_key.strip() or self.embedding_api_key

    @property
    def embedding_configured(self) -> bool:
        return self.embedding_api_key.strip() not in API_KEY_PLACEHOLDERS

    @property
    def chat_configured(self) -> bool:
        return bool(self.chat_model.strip()) and self.effective_chat_api_key.strip() not in API_KEY_PLACEHOLDERS

    @property
    def ocr_configured(self) -> bool:
        return (
            self.ocr_enabled
            and bool(self.ocr_model.strip())
            and self.effective_ocr_api_key.strip() not in API_KEY_PLACEHOLDERS
        )

    @property
    def rerank_configured(self) -> bool:
        return (
            self.rerank_enabled
            and bool(self.rerank_model.strip())
            and self.effective_rerank_api_key.strip() not in API_KEY_PLACEHOLDERS
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        legacy_database = self.data_dir / "knowledge_forge.db"
        if legacy_database.exists() and not self.database_path.exists():
            legacy_database.replace(self.database_path)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
