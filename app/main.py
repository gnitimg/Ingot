from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.database import Database
from app.schemas import (
    ChatRequest,
    ExportRequest,
    KnowledgeBaseCreate,
    KnowledgeBasePasswordUpdate,
    KnowledgeBaseUnlock,
    SearchRequest,
    SettingsUpdate,
)
from app.services.ai_client import AIClient, AIServiceError
from app.services.exporter import KnowledgeBaseExporter
from app.services.graph_rag import GraphRAGService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService


settings = get_settings()
db = Database(settings.database_path)
exporter = KnowledgeBaseExporter(db)
static_dir = Path(__file__).parent / "static"
graph_tasks: dict[str, asyncio.Task[None]] = {}
GRAPH_USABLE_STATUSES = {"ready", "partial"}
kb_access_tokens: dict[str, set[str]] = {}

DEVICE_COOKIE_NAME = "ingot_device_settings"
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
DEVICE_SETTING_FIELDS = (
    "embedding_base_url",
    "embedding_api_key",
    "embedding_model",
    "embedding_batch_size",
    "embedding_timeout",
    "chat_base_url",
    "chat_api_key",
    "chat_model",
    "chat_timeout",
    "chat_temperature",
    "chat_max_tokens",
    "ocr_enabled",
    "ocr_base_url",
    "ocr_api_key",
    "ocr_model",
    "ocr_timeout",
    "ocr_concurrency",
    "ocr_min_text_chars",
    "ocr_max_pages",
    "ocr_render_dpi",
    "rerank_enabled",
    "rerank_base_url",
    "rerank_api_key",
    "rerank_model",
    "rerank_candidates",
    "rerank_timeout",
    "chunk_size",
    "chunk_overlap",
    "default_top_k",
    "qa_evidence_count",
    "graph_concurrency",
    "graph_max_chunks",
    "graph_chunk_timeout",
    "graph_build_timeout",
    "graph_retry_rounds",
    "graph_retry_backoff",
    "graph_success_threshold",
    "graph_llm_entity_matching",
)
_cookie_secret = settings.device_cookie_secret.strip() or secrets.token_urlsafe(48)
_cookie_key = base64.urlsafe_b64encode(hashlib.sha256(_cookie_secret.encode("utf-8")).digest())
device_settings_cipher = Fernet(_cookie_key)


@dataclass
class RuntimeServices:
    settings: Settings
    ai: AIClient
    ingestion: IngestionService
    graph_rag: GraphRAGService
    retrieval: RetrievalService

    async def aclose(self) -> None:
        await self.ai.aclose()


def _runtime_from_settings(device_settings: Settings) -> RuntimeServices:
    ai = AIClient(device_settings)
    return RuntimeServices(
        settings=device_settings,
        ai=ai,
        ingestion=IngestionService(db, ai, device_settings),
        graph_rag=GraphRAGService(db, ai, device_settings),
        retrieval=RetrievalService(db, ai, device_settings),
    )


def _default_device_settings() -> Settings:
    values = settings.model_dump()
    values.update(
        embedding_base_url="",
        embedding_api_key="",
        chat_base_url="",
        chat_api_key="",
        ocr_base_url="",
        ocr_api_key="",
        rerank_base_url="",
        rerank_api_key="",
    )
    return Settings(_env_file=None, **values)


def _device_settings_from_request(request: Request) -> tuple[Settings, bool]:
    token = request.cookies.get(DEVICE_COOKIE_NAME)
    if not token:
        return _default_device_settings(), False
    try:
        payload = json.loads(
            device_settings_cipher.decrypt(
                token.encode("ascii"),
                ttl=DEVICE_COOKIE_MAX_AGE,
            )
        )
        if not isinstance(payload, dict):
            raise ValueError
        values = settings.model_dump()
        values.update({field: payload[field] for field in DEVICE_SETTING_FIELDS if field in payload})
        return Settings(_env_file=None, **values), True
    except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return _default_device_settings(), False


def _cookie_is_secure(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _store_device_settings(
    response: Response,
    request: Request,
    device_settings: Settings,
) -> None:
    payload = {
        field: getattr(device_settings, field)
        for field in DEVICE_SETTING_FIELDS
    }
    token = device_settings_cipher.encrypt(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    if len(token) > 3800:
        raise HTTPException(status_code=413, detail="设备配置超过 Cookie 容量限制")
    response.set_cookie(
        DEVICE_COOKIE_NAME,
        token,
        max_age=DEVICE_COOKIE_MAX_AGE,
        path="/api",
        secure=_cookie_is_secure(request),
        httponly=True,
        samesite="strict",
    )


def _retained_secret(replacement: SecretStr | None, current: str) -> str:
    if replacement is None:
        return current
    value = replacement.get_secret_value().strip()
    return value or current


def _candidate_settings(payload: SettingsUpdate, current: Settings) -> Settings:
    values = current.model_dump()
    values.update(
        embedding_base_url=payload.embedding.base_url,
        embedding_api_key=_retained_secret(payload.embedding.api_key, current.embedding_api_key),
        embedding_model=payload.embedding.model,
        embedding_batch_size=payload.embedding.batch_size,
        embedding_timeout=payload.embedding.timeout,
        chat_base_url="" if payload.chat.use_embedding_provider else payload.chat.base_url,
        chat_api_key=(
            ""
            if payload.chat.use_embedding_provider
            else _retained_secret(payload.chat.api_key, current.chat_api_key)
        ),
        chat_model=payload.chat.model,
        chat_timeout=payload.chat.timeout,
        chat_temperature=payload.chat.temperature,
        chat_max_tokens=payload.chat.max_tokens,
        qa_evidence_count=payload.chat.evidence_count,
        ocr_enabled=payload.ocr.enabled,
        ocr_base_url="" if payload.ocr.use_embedding_provider else payload.ocr.base_url,
        ocr_api_key=(
            ""
            if payload.ocr.use_embedding_provider
            else _retained_secret(payload.ocr.api_key, current.ocr_api_key)
        ),
        ocr_model=payload.ocr.model,
        ocr_timeout=payload.ocr.timeout,
        ocr_concurrency=payload.ocr.concurrency,
        ocr_min_text_chars=payload.ocr.min_text_chars,
        ocr_max_pages=payload.ocr.max_pages,
        ocr_render_dpi=payload.ocr.render_dpi,
        rerank_enabled=payload.rerank.enabled,
        rerank_base_url="" if payload.rerank.use_embedding_provider else payload.rerank.base_url,
        rerank_api_key=(
            ""
            if payload.rerank.use_embedding_provider
            else _retained_secret(payload.rerank.api_key, current.rerank_api_key)
        ),
        rerank_model=payload.rerank.model,
        rerank_candidates=payload.rerank.candidates,
        rerank_timeout=payload.rerank.timeout,
        chunk_size=payload.chunking.chunk_size,
        chunk_overlap=payload.chunking.chunk_overlap,
        default_top_k=payload.chunking.default_top_k,
        graph_concurrency=payload.graph.concurrency,
        graph_max_chunks=payload.graph.max_chunks,
        graph_chunk_timeout=payload.graph.chunk_timeout,
        graph_build_timeout=payload.graph.build_timeout,
        graph_retry_rounds=payload.graph.retry_rounds,
        graph_retry_backoff=payload.graph.retry_backoff,
        graph_success_threshold=(
            current.graph_success_threshold
            if payload.graph.success_threshold is None
            else payload.graph.success_threshold
        ),
        graph_llm_entity_matching=(
            current.graph_llm_entity_matching
            if payload.graph.llm_entity_matching is None
            else payload.graph.llm_entity_matching
        ),
    )
    return Settings(_env_file=None, **values)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    db.initialize()
    try:
        yield
    finally:
        active_tasks = list(graph_tasks.items())
        for _, task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*(task for _, task in active_tasks), return_exceptions=True)
            for kb_id, _ in active_tasks:
                knowledge_base = db.get_knowledge_base(kb_id)
                if knowledge_base and knowledge_base["graph_status"] == "building":
                    db.pause_graph_build(kb_id, "服务停止导致图谱构建暂停，可从当前检查点继续")


app = FastAPI(
    title="Ingot API",
    description="Local-first RAG + OCR + GraphRAG knowledge-base application",
    version="1.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def require_knowledge_base(kb_id: str) -> dict:
    knowledge_base = db.get_knowledge_base(kb_id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return knowledge_base


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_kb_access_token(kb_id: str) -> str:
    token = secrets.token_urlsafe(32)
    kb_access_tokens.setdefault(kb_id, set()).add(_token_digest(token))
    return token


def revoke_kb_access_tokens(kb_id: str) -> None:
    kb_access_tokens.pop(kb_id, None)


def require_knowledge_base_access(kb_id: str, request: Request) -> dict:
    knowledge_base = require_knowledge_base(kb_id)
    if not knowledge_base.get("has_password"):
        return knowledge_base
    token = request.headers.get("X-Ingot-KB-Token", "")
    token_digest = _token_digest(token) if token else ""
    if not any(
        hmac.compare_digest(token_digest, stored)
        for stored in kb_access_tokens.get(kb_id, set())
    ):
        raise HTTPException(status_code=401, detail="请先输入密码解锁知识库")
    return knowledge_base


def model_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


async def run_graph_build(
    kb_id: str,
    runtime: RuntimeServices,
    *,
    resume: bool = False,
    finalize_partial: bool = False,
) -> None:
    try:
        if finalize_partial:
            await runtime.graph_rag.finalize_partial(kb_id)
        else:
            await runtime.graph_rag.rebuild(kb_id, resume=resume)
    finally:
        await runtime.aclose()
        current_task = asyncio.current_task()
        if graph_tasks.get(kb_id) is current_task:
            graph_tasks.pop(kb_id, None)


def forget_graph_task(kb_id: str, task: asyncio.Task[None]) -> None:
    knowledge_base = db.get_knowledge_base(kb_id)
    if knowledge_base and knowledge_base["graph_status"] == "building":
        if task.cancelled():
            db.pause_graph_build(kb_id, "图谱构建已暂停，可从当前检查点继续")
        elif task.exception() is not None:
            db.set_graph_status(kb_id, "error", f"图谱任务异常退出：{task.exception()}")
    if graph_tasks.get(kb_id) is task:
        graph_tasks.pop(kb_id, None)


async def cancel_active_graph_task(kb_id: str) -> None:
    task = graph_tasks.get(kb_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def launch_graph_task(
    kb_id: str,
    runtime: RuntimeServices,
    *,
    resume: bool,
    finalize_partial: bool = False,
) -> None:
    task = asyncio.create_task(
        run_graph_build(
            kb_id,
            runtime,
            resume=resume,
            finalize_partial=finalize_partial,
        ),
        name=(
            f"graph-finalize-{kb_id}"
            if finalize_partial
            else f"graph-{'resume' if resume else 'build'}-{kb_id}"
        ),
    )
    graph_tasks[kb_id] = task
    task.add_done_callback(lambda completed: forget_graph_task(kb_id, completed))


def begin_partial_finalization(
    knowledge_base: dict,
    runtime: RuntimeServices,
) -> dict | None:
    if knowledge_base.get("graph_status") != "paused":
        return None
    kb_id = str(knowledge_base["id"])
    checkpoint = db.graph_checkpoint_stats(kb_id)
    success_ratio = (
        checkpoint["succeeded"] / checkpoint["total"] * 100
        if checkpoint["total"]
        else 0.0
    )
    if (
        not checkpoint["succeeded"]
        or success_ratio < runtime.settings.graph_success_threshold
    ):
        return None
    db.update_graph_progress(
        kb_id,
        stage=(
            "matching"
            if runtime.settings.graph_llm_entity_matching
            else "communities"
        ),
        current=checkpoint["succeeded"],
        total=checkpoint["total"],
        failed_chunks=checkpoint["total"] - checkpoint["succeeded"],
    )
    launch_graph_task(kb_id, runtime, resume=False, finalize_partial=True)
    return {
        "status": "building",
        "resumed": True,
        "finalizing_partial": True,
        "stage": "matching" if runtime.settings.graph_llm_entity_matching else "communities",
        "current": checkpoint["succeeded"],
        "total": checkpoint["total"],
        "failed": checkpoint["failed"],
    }


def resume_graph_from_checkpoint(
    kb_id: str,
    knowledge_base: dict,
    runtime: RuntimeServices,
) -> dict:
    checkpoint = db.graph_checkpoint_stats(kb_id)
    if not checkpoint["total"]:
        raise HTTPException(status_code=409, detail="构建检查点不存在，请从零重新构建")
    finalizing = begin_partial_finalization(knowledge_base, runtime)
    if finalizing is not None:
        return finalizing
    db.resume_graph_build(kb_id)
    checkpoint = db.graph_checkpoint_stats(kb_id)
    launch_graph_task(kb_id, runtime, resume=True)
    return {
        "status": "building",
        "resumed": True,
        "stage": knowledge_base["graph_stage"],
        "current": checkpoint["succeeded"],
        "total": checkpoint["total"],
        "failed": checkpoint["failed"],
    }


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health(request: Request) -> dict:
    device_settings, stored = _device_settings_from_request(request)
    return {
        "status": "ok",
        "device_settings_present": stored,
        "embedding_configured": device_settings.embedding_configured,
        "chat_configured": device_settings.chat_configured,
        "ocr_configured": device_settings.ocr_configured,
        "rerank_configured": device_settings.rerank_configured,
        "embedding_model": device_settings.embedding_model,
        "chat_model": device_settings.chat_model,
        "ocr_model": device_settings.ocr_model,
        "rerank_model": device_settings.rerank_model,
    }


@app.get("/api/settings")
async def public_settings(request: Request) -> dict:
    device_settings, stored = _device_settings_from_request(request)
    return _public_settings(device_settings, stored)


def _public_settings(device_settings: Settings, stored: bool = True) -> dict:
    return {
        "device_settings_present": stored,
        "settings_storage": "encrypted_device_cookie",
        "embedding_base_url": device_settings.embedding_base_url,
        "embedding_model": device_settings.embedding_model,
        "embedding_configured": device_settings.embedding_configured,
        "embedding_batch_size": device_settings.embedding_batch_size,
        "embedding_timeout": device_settings.embedding_timeout,
        "chat_base_url": device_settings.effective_chat_base_url,
        "chat_model": device_settings.chat_model,
        "chat_configured": device_settings.chat_configured,
        "chat_uses_embedding_provider": (
            not device_settings.chat_base_url.strip() and not device_settings.chat_api_key.strip()
        ),
        "chat_timeout": device_settings.chat_timeout,
        "chat_temperature": device_settings.chat_temperature,
        "chat_max_tokens": device_settings.chat_max_tokens,
        "qa_evidence_count": device_settings.qa_evidence_count,
        "ocr_enabled": device_settings.ocr_enabled,
        "ocr_base_url": device_settings.effective_ocr_base_url,
        "ocr_model": device_settings.ocr_model,
        "ocr_configured": device_settings.ocr_configured,
        "ocr_uses_embedding_provider": (
            not device_settings.ocr_base_url.strip() and not device_settings.ocr_api_key.strip()
        ),
        "ocr_timeout": device_settings.ocr_timeout,
        "ocr_concurrency": device_settings.ocr_concurrency,
        "ocr_min_text_chars": device_settings.ocr_min_text_chars,
        "ocr_max_pages": device_settings.ocr_max_pages,
        "ocr_render_dpi": device_settings.ocr_render_dpi,
        "rerank_enabled": device_settings.rerank_enabled,
        "rerank_base_url": device_settings.effective_rerank_base_url,
        "rerank_model": device_settings.rerank_model,
        "rerank_configured": device_settings.rerank_configured,
        "rerank_uses_embedding_provider": (
            not device_settings.rerank_base_url.strip() and not device_settings.rerank_api_key.strip()
        ),
        "rerank_candidates": device_settings.rerank_candidates,
        "rerank_timeout": device_settings.rerank_timeout,
        "chunk_size": device_settings.chunk_size,
        "chunk_overlap": device_settings.chunk_overlap,
        "default_top_k": device_settings.default_top_k,
        "max_upload_mb": settings.max_upload_mb,
        "graph_concurrency": device_settings.graph_concurrency,
        "graph_max_chunks": device_settings.graph_max_chunks,
        "graph_chunk_timeout": device_settings.graph_chunk_timeout,
        "graph_build_timeout": device_settings.graph_build_timeout,
        "graph_retry_rounds": device_settings.graph_retry_rounds,
        "graph_retry_backoff": device_settings.graph_retry_backoff,
        "graph_success_threshold": device_settings.graph_success_threshold,
        "graph_llm_entity_matching": device_settings.graph_llm_entity_matching,
    }


@app.put("/api/settings")
async def update_settings(payload: SettingsUpdate, request: Request) -> Response:
    current, _ = _device_settings_from_request(request)
    candidate = _candidate_settings(payload, current)
    response = JSONResponse(_public_settings(candidate))
    _store_device_settings(response, request, candidate)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/knowledge-bases")
async def list_knowledge_bases() -> list[dict]:
    return db.list_knowledge_bases()


@app.post("/api/knowledge-bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(payload: KnowledgeBaseCreate) -> dict:
    password = payload.password.strip()
    if not password:
        raise HTTPException(status_code=422, detail="知识库密码不能为空")
    knowledge_base = db.create_knowledge_base(
        payload.name.strip(), payload.description.strip(), password
    )
    return {**knowledge_base, "access_token": issue_kb_access_token(knowledge_base["id"])}


@app.post("/api/knowledge-bases/{kb_id}/unlock")
async def unlock_knowledge_base(kb_id: str, payload: KnowledgeBaseUnlock) -> dict:
    knowledge_base = require_knowledge_base(kb_id)
    if not db.verify_knowledge_base_password(kb_id, payload.password):
        raise HTTPException(status_code=401, detail="知识库密码不正确")
    return {
        "access_token": issue_kb_access_token(kb_id),
        "knowledge_base": knowledge_base,
    }


@app.put("/api/knowledge-bases/{kb_id}/password")
async def update_knowledge_base_password(
    kb_id: str, payload: KnowledgeBasePasswordUpdate, request: Request
) -> dict:
    require_knowledge_base_access(kb_id, request)
    new_password = payload.new_password.strip()
    if not new_password:
        raise HTTPException(status_code=422, detail="新密码不能为空")
    if not db.change_knowledge_base_password(kb_id, payload.old_password, new_password):
        raise HTTPException(status_code=403, detail="旧密码不正确")
    revoke_kb_access_tokens(kb_id)
    knowledge_base = require_knowledge_base(kb_id)
    return {
        "access_token": issue_kb_access_token(kb_id),
        "knowledge_base": knowledge_base,
    }


@app.get("/api/knowledge-bases/{kb_id}")
async def get_knowledge_base(kb_id: str, request: Request) -> dict:
    return require_knowledge_base_access(kb_id, request)


@app.delete("/api/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb_id: str, request: Request) -> None:
    require_knowledge_base_access(kb_id, request)
    await cancel_active_graph_task(kb_id)
    paths = db.delete_knowledge_base(kb_id)
    revoke_kb_access_tokens(kb_id)
    for path in paths:
        IngestionService.remove_file(path)
    IngestionService.remove_tree(settings.upload_dir / kb_id)


@app.get("/api/knowledge-bases/{kb_id}/documents")
async def list_documents(kb_id: str, request: Request) -> list[dict]:
    require_knowledge_base_access(kb_id, request)
    return db.list_documents(kb_id)


@app.post("/api/knowledge-bases/{kb_id}/documents")
async def upload_documents(
    kb_id: str, request: Request, files: list[UploadFile] = File(...)
) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
    if knowledge_base["graph_status"] in {"building", "paused"}:
        await cancel_active_graph_task(kb_id)
        db.invalidate_graph(
            kb_id,
            "文档发生变更，原构建检查点和部分图谱已清空；下次构建将从零开始",
        )
    device_settings, _ = _device_settings_from_request(request)
    runtime = _runtime_from_settings(device_settings)
    results = []
    try:
        for upload in files:
            try:
                results.append(await runtime.ingestion.ingest_upload(kb_id, upload))
            except (ValueError, AIServiceError) as exc:
                await upload.close()
                results.append(
                    {"id": None, "filename": upload.filename, "status": "error", "chunk_count": 0, "error": str(exc)}
                )
    finally:
        await runtime.aclose()
    return {"documents": results}


@app.delete("/api/knowledge-bases/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(kb_id: str, document_id: str, request: Request) -> None:
    require_knowledge_base_access(kb_id, request)
    await cancel_active_graph_task(kb_id)
    path = db.delete_document(kb_id, document_id)
    if path is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    IngestionService.remove_file(path)


@app.post("/api/knowledge-bases/{kb_id}/search")
async def search(kb_id: str, payload: SearchRequest, request: Request) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="知识库中还没有可检索的文档")
    if (
        payload.mode.startswith("graph")
        and knowledge_base["graph_status"] not in GRAPH_USABLE_STATUSES
    ):
        raise HTTPException(status_code=409, detail="图谱尚未构建完成")
    device_settings, _ = _device_settings_from_request(request)
    runtime = _runtime_from_settings(device_settings)
    try:
        return await runtime.retrieval.search(
            kb_id,
            payload.query,
            payload.mode,
            payload.top_k,
        )
    except AIServiceError as exc:
        raise model_error(exc) from exc
    finally:
        await runtime.aclose()


@app.post("/api/knowledge-bases/{kb_id}/chat")
async def chat(kb_id: str, payload: ChatRequest, request: Request) -> StreamingResponse:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="请先上传并解析文档")
    if (
        payload.mode in {"graph_local", "graph_global"}
        and knowledge_base["graph_status"] not in GRAPH_USABLE_STATUSES
    ):
        raise HTTPException(status_code=409, detail="图谱尚未构建完成；可先使用向量模式")
    effective_mode = payload.mode
    if payload.mode == "hybrid" and knowledge_base["graph_status"] not in GRAPH_USABLE_STATUSES:
        effective_mode = "vector"
    device_settings, _ = _device_settings_from_request(request)
    runtime = _runtime_from_settings(device_settings)
    try:
        result = await runtime.retrieval.search(
            kb_id,
            payload.query,
            effective_mode,
            payload.top_k,
        )
    except AIServiceError as exc:
        await runtime.aclose()
        raise model_error(exc) from exc
    messages = runtime.retrieval.build_messages(
        payload.query,
        result,
        [{"role": message.role, "content": message.content} for message in payload.history],
    )

    async def event_stream():
        meta = {
            "mode": effective_mode,
            "sources": result["chunks"],
            "facts": result["facts"],
            "communities": result["communities"],
            "rerank_used": result["rerank_used"],
            "warnings": result["warnings"],
        }
        yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"
        try:
            async for token in runtime.ai.chat_stream(messages):
                yield f"event: delta\ndata: {json.dumps({'content': token}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except AIServiceError as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            await runtime.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/knowledge-bases/{kb_id}/graph/rebuild", status_code=status.HTTP_202_ACCEPTED)
async def rebuild_graph(kb_id: str, request: Request) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    active_task = graph_tasks.get(kb_id)
    if active_task and not active_task.done():
        raise HTTPException(status_code=409, detail="图谱正在构建")
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="请先上传并解析文档")
    if knowledge_base["graph_status"] == "building":
        db.pause_graph_build(kb_id, "构建任务已丢失，已恢复为暂停状态")
        knowledge_base = require_knowledge_base(kb_id)
    if knowledge_base["graph_status"] in {"error", "paused"}:
        db.recover_legacy_graph_timeout(kb_id)
        knowledge_base = require_knowledge_base(kb_id)
    device_settings, _ = _device_settings_from_request(request)
    runtime = _runtime_from_settings(device_settings)
    # Compatibility guard: old browser bundles only know /rebuild. A persisted
    # checkpoint is authoritative regardless of the legacy status value; never
    # let an old button accidentally clear partial graph data.
    checkpoint = db.graph_checkpoint_stats(kb_id)
    if checkpoint["total"]:
        if knowledge_base["graph_status"] not in {"paused", "partial"}:
            db.pause_graph_build(kb_id, "检测到未完成的构建检查点，可从当前进度继续")
            knowledge_base = require_knowledge_base(kb_id)
        try:
            return resume_graph_from_checkpoint(kb_id, knowledge_base, runtime)
        except Exception:
            await runtime.aclose()
            raise
    total = int(knowledge_base["chunk_count"])
    if device_settings.graph_max_chunks:
        total = min(total, device_settings.graph_max_chunks)
    db.start_graph_build(kb_id, total)
    launch_graph_task(kb_id, runtime, resume=False)
    return {"status": "building", "resumed": False, "stage": "queued", "current": 0, "total": total}


@app.post("/api/knowledge-bases/{kb_id}/graph/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_graph(kb_id: str, request: Request) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if knowledge_base["graph_status"] in {"error", "paused"} and db.recover_legacy_graph_timeout(kb_id):
        knowledge_base = require_knowledge_base(kb_id)
    if knowledge_base["graph_status"] not in {"building", "paused"}:
        raise HTTPException(status_code=409, detail="当前没有可停止的图谱任务")
    await cancel_active_graph_task(kb_id)
    db.invalidate_graph(
        kb_id,
        "图谱构建已由用户停止；部分结果和检查点已清空，下次构建将从零开始",
        stage="stopped",
    )
    return {"status": "stopped"}


@app.post("/api/knowledge-bases/{kb_id}/graph/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_graph(kb_id: str, request: Request) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    active_task = graph_tasks.get(kb_id)
    if active_task and not active_task.done():
        raise HTTPException(status_code=409, detail="图谱正在构建")
    if knowledge_base["graph_status"] in {"error", "paused"} and db.recover_legacy_graph_timeout(kb_id):
        knowledge_base = require_knowledge_base(kb_id)
    checkpoint = db.graph_checkpoint_stats(kb_id)
    if checkpoint["total"] and knowledge_base["graph_status"] not in {"paused", "partial"}:
        db.pause_graph_build(kb_id, "检测到未完成的构建检查点，可从当前进度继续")
        knowledge_base = require_knowledge_base(kb_id)
    if knowledge_base["graph_status"] not in {"paused", "partial"}:
        raise HTTPException(status_code=409, detail="当前图谱任务不处于暂停状态")
    device_settings, _ = _device_settings_from_request(request)
    runtime = _runtime_from_settings(device_settings)
    try:
        return resume_graph_from_checkpoint(kb_id, knowledge_base, runtime)
    except Exception:
        await runtime.aclose()
        raise


@app.get("/api/knowledge-bases/{kb_id}/graph")
async def get_graph(kb_id: str, request: Request, limit: int = 500) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    data = db.graph_data(kb_id, min(max(limit, 20), 1000))
    communities = db.fetch_all(
        """SELECT id, title, summary, member_count FROM communities
           WHERE kb_id = ? ORDER BY member_count DESC LIMIT 100""",
        (kb_id,),
    )
    return {
        "status": knowledge_base["graph_status"],
        "error": knowledge_base["graph_error"],
        "total_nodes": knowledge_base["entity_count"],
        "total_edges": knowledge_base["relationship_count"],
        "total_communities": knowledge_base["community_count"],
        "is_truncated": (
            len(data["nodes"]) < int(knowledge_base["entity_count"])
            or len(data["edges"]) < int(knowledge_base["relationship_count"])
            or len(communities) < int(knowledge_base["community_count"])
        ),
        **data,
        "communities": communities,
    }


@app.get("/api/knowledge-bases/{kb_id}/exports/options")
async def export_options(kb_id: str, request: Request) -> list[dict]:
    require_knowledge_base_access(kb_id, request)
    return await asyncio.to_thread(exporter.options, kb_id)


@app.post("/api/knowledge-bases/{kb_id}/exports")
async def export_knowledge_base(
    kb_id: str,
    payload: ExportRequest,
    request: Request,
) -> Response:
    require_knowledge_base_access(kb_id, request)
    try:
        artifact = await asyncio.to_thread(
            exporter.build,
            kb_id,
            [selection.model_dump() for selection in payload.selections],
            payload.bundle,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", artifact.filename).strip("-") or "export"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(artifact.filename)}"
    )
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
        },
    )
