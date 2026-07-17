from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.database import Database
from app.schemas import (
    ChatRequest,
    KnowledgeBaseCreate,
    KnowledgeBasePasswordUpdate,
    KnowledgeBaseUnlock,
    SearchRequest,
    SettingsUpdate,
)
from app.services.ai_client import AIClient, AIServiceError
from app.services.graph_rag import GraphRAGService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from init import ENV_PATH, write_env_updates


settings = get_settings()
db = Database(settings.database_path)
ai = AIClient(settings)
ingestion = IngestionService(db, ai, settings)
graph_rag = GraphRAGService(db, ai, settings)
retrieval = RetrievalService(db, ai, settings)
static_dir = Path(__file__).parent / "static"
settings_update_lock = asyncio.Lock()
graph_tasks: dict[str, asyncio.Task[None]] = {}
kb_access_tokens: dict[str, set[str]] = {}

RUNTIME_ENV_KEYS = {
    "embedding_base_url": "EMBEDDING_BASE_URL",
    "embedding_api_key": "EMBEDDING_API_KEY",
    "embedding_model": "EMBEDDING_MODEL",
    "embedding_batch_size": "EMBEDDING_BATCH_SIZE",
    "embedding_timeout": "EMBEDDING_TIMEOUT",
    "chat_base_url": "CHAT_BASE_URL",
    "chat_api_key": "CHAT_API_KEY",
    "chat_model": "CHAT_MODEL",
    "chat_timeout": "CHAT_TIMEOUT",
    "chat_temperature": "CHAT_TEMPERATURE",
    "chat_max_tokens": "CHAT_MAX_TOKENS",
    "ocr_enabled": "OCR_ENABLED",
    "ocr_base_url": "OCR_BASE_URL",
    "ocr_api_key": "OCR_API_KEY",
    "ocr_model": "OCR_MODEL",
    "ocr_timeout": "OCR_TIMEOUT",
    "ocr_concurrency": "OCR_CONCURRENCY",
    "ocr_min_text_chars": "OCR_MIN_TEXT_CHARS",
    "ocr_max_pages": "OCR_MAX_PAGES",
    "ocr_render_dpi": "OCR_RENDER_DPI",
    "rerank_enabled": "RERANK_ENABLED",
    "rerank_base_url": "RERANK_BASE_URL",
    "rerank_api_key": "RERANK_API_KEY",
    "rerank_model": "RERANK_MODEL",
    "rerank_candidates": "RERANK_CANDIDATES",
    "rerank_timeout": "RERANK_TIMEOUT",
    "chunk_size": "CHUNK_SIZE",
    "chunk_overlap": "CHUNK_OVERLAP",
    "default_top_k": "DEFAULT_TOP_K",
    "qa_evidence_count": "QA_EVIDENCE_COUNT",
    "graph_concurrency": "GRAPH_CONCURRENCY",
    "graph_max_chunks": "GRAPH_MAX_CHUNKS",
    "graph_chunk_timeout": "GRAPH_CHUNK_TIMEOUT",
    "graph_build_timeout": "GRAPH_BUILD_TIMEOUT",
    "graph_retry_rounds": "GRAPH_RETRY_ROUNDS",
    "graph_retry_backoff": "GRAPH_RETRY_BACKOFF",
}


def _env_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _retained_secret(replacement: SecretStr | None, current: str) -> str:
    if replacement is None:
        return current
    value = replacement.get_secret_value().strip()
    return value or current


def _candidate_settings(payload: SettingsUpdate) -> Settings:
    values = settings.model_dump()
    values.update(
        embedding_base_url=payload.embedding.base_url,
        embedding_api_key=_retained_secret(payload.embedding.api_key, settings.embedding_api_key),
        embedding_model=payload.embedding.model,
        embedding_batch_size=payload.embedding.batch_size,
        embedding_timeout=payload.embedding.timeout,
        chat_base_url="" if payload.chat.use_embedding_provider else payload.chat.base_url,
        chat_api_key=(
            ""
            if payload.chat.use_embedding_provider
            else _retained_secret(payload.chat.api_key, settings.chat_api_key)
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
            else _retained_secret(payload.ocr.api_key, settings.ocr_api_key)
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
            else _retained_secret(payload.rerank.api_key, settings.rerank_api_key)
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
        await ai.aclose()


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


async def run_graph_build(kb_id: str, *, resume: bool = False) -> None:
    try:
        await graph_rag.rebuild(kb_id, resume=resume)
    finally:
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


def launch_graph_task(kb_id: str, *, resume: bool) -> None:
    task = asyncio.create_task(
        run_graph_build(kb_id, resume=resume),
        name=f"graph-{'resume' if resume else 'build'}-{kb_id}",
    )
    graph_tasks[kb_id] = task
    task.add_done_callback(lambda completed: forget_graph_task(kb_id, completed))


def resume_graph_from_checkpoint(kb_id: str, knowledge_base: dict) -> dict:
    checkpoint = db.graph_checkpoint_stats(kb_id)
    if not checkpoint["total"]:
        raise HTTPException(status_code=409, detail="构建检查点不存在，请从零重新构建")
    db.resume_graph_build(kb_id)
    checkpoint = db.graph_checkpoint_stats(kb_id)
    launch_graph_task(kb_id, resume=True)
    return {
        "status": "building",
        "resumed": True,
        "stage": knowledge_base["graph_stage"],
        "current": checkpoint["succeeded"],
        "total": checkpoint["total"],
        "failed": checkpoint["failed"],
    }


def require_local_request(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        is_local = ip_address(host).is_loopback
    except ValueError:
        is_local = host == "testclient"
    if not is_local:
        raise HTTPException(status_code=403, detail="运行配置只能从本机修改")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "embedding_configured": settings.embedding_configured,
        "chat_configured": settings.chat_configured,
        "ocr_configured": settings.ocr_configured,
        "rerank_configured": settings.rerank_configured,
        "embedding_model": settings.embedding_model,
        "chat_model": settings.chat_model,
        "ocr_model": settings.ocr_model,
        "rerank_model": settings.rerank_model,
    }


@app.get("/api/settings")
async def public_settings() -> dict:
    return {
        "embedding_base_url": settings.embedding_base_url,
        "embedding_model": settings.embedding_model,
        "embedding_configured": settings.embedding_configured,
        "embedding_batch_size": settings.embedding_batch_size,
        "embedding_timeout": settings.embedding_timeout,
        "chat_base_url": settings.effective_chat_base_url,
        "chat_model": settings.chat_model,
        "chat_configured": settings.chat_configured,
        "chat_uses_embedding_provider": (
            not settings.chat_base_url.strip() and not settings.chat_api_key.strip()
        ),
        "chat_timeout": settings.chat_timeout,
        "chat_temperature": settings.chat_temperature,
        "chat_max_tokens": settings.chat_max_tokens,
        "qa_evidence_count": settings.qa_evidence_count,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_base_url": settings.effective_ocr_base_url,
        "ocr_model": settings.ocr_model,
        "ocr_configured": settings.ocr_configured,
        "ocr_uses_embedding_provider": (
            not settings.ocr_base_url.strip() and not settings.ocr_api_key.strip()
        ),
        "ocr_timeout": settings.ocr_timeout,
        "ocr_concurrency": settings.ocr_concurrency,
        "ocr_min_text_chars": settings.ocr_min_text_chars,
        "ocr_max_pages": settings.ocr_max_pages,
        "ocr_render_dpi": settings.ocr_render_dpi,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_base_url": settings.effective_rerank_base_url,
        "rerank_model": settings.rerank_model,
        "rerank_configured": settings.rerank_configured,
        "rerank_uses_embedding_provider": (
            not settings.rerank_base_url.strip() and not settings.rerank_api_key.strip()
        ),
        "rerank_candidates": settings.rerank_candidates,
        "rerank_timeout": settings.rerank_timeout,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "default_top_k": settings.default_top_k,
        "max_upload_mb": settings.max_upload_mb,
        "graph_concurrency": settings.graph_concurrency,
        "graph_max_chunks": settings.graph_max_chunks,
        "graph_chunk_timeout": settings.graph_chunk_timeout,
        "graph_build_timeout": settings.graph_build_timeout,
        "graph_retry_rounds": settings.graph_retry_rounds,
        "graph_retry_backoff": settings.graph_retry_backoff,
    }


@app.put("/api/settings")
async def update_settings(payload: SettingsUpdate, request: Request) -> dict:
    require_local_request(request)
    async with settings_update_lock:
        candidate = _candidate_settings(payload)
        env_updates = {
            env_key: _env_value(getattr(candidate, field_name))
            for field_name, env_key in RUNTIME_ENV_KEYS.items()
        }
        try:
            await asyncio.to_thread(write_env_updates, env_updates, ENV_PATH)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="无法安全写入本地 .env 配置") from exc

        for field_name in RUNTIME_ENV_KEYS:
            setattr(settings, field_name, getattr(candidate, field_name))

    return await public_settings()


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
        ingestion.remove_file(path)
    ingestion.remove_tree(settings.upload_dir / kb_id)


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
    results = []
    for upload in files:
        try:
            results.append(await ingestion.ingest_upload(kb_id, upload))
        except (ValueError, AIServiceError) as exc:
            await upload.close()
            results.append(
                {"id": None, "filename": upload.filename, "status": "error", "chunk_count": 0, "error": str(exc)}
            )
    return {"documents": results}


@app.delete("/api/knowledge-bases/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(kb_id: str, document_id: str, request: Request) -> None:
    require_knowledge_base_access(kb_id, request)
    await cancel_active_graph_task(kb_id)
    path = db.delete_document(kb_id, document_id)
    if path is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    ingestion.remove_file(path)


@app.post("/api/knowledge-bases/{kb_id}/search")
async def search(kb_id: str, payload: SearchRequest, request: Request) -> dict:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="知识库中还没有可检索的文档")
    if payload.mode.startswith("graph") and knowledge_base["graph_status"] != "ready":
        raise HTTPException(status_code=409, detail="图谱尚未构建完成")
    try:
        return await retrieval.search(kb_id, payload.query, payload.mode, payload.top_k)
    except AIServiceError as exc:
        raise model_error(exc) from exc


@app.post("/api/knowledge-bases/{kb_id}/chat")
async def chat(kb_id: str, payload: ChatRequest, request: Request) -> StreamingResponse:
    knowledge_base = require_knowledge_base_access(kb_id, request)
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="请先上传并解析文档")
    if payload.mode in {"graph_local", "graph_global"} and knowledge_base["graph_status"] != "ready":
        raise HTTPException(status_code=409, detail="图谱尚未构建完成；可先使用向量模式")
    effective_mode = payload.mode
    if payload.mode == "hybrid" and knowledge_base["graph_status"] != "ready":
        effective_mode = "vector"
    try:
        result = await retrieval.search(kb_id, payload.query, effective_mode, payload.top_k)
    except AIServiceError as exc:
        raise model_error(exc) from exc
    messages = retrieval.build_messages(
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
            async for token in ai.chat_stream(messages):
                yield f"event: delta\ndata: {json.dumps({'content': token}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except AIServiceError as exc:
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)}, ensure_ascii=False)}\n\n"

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
    # Compatibility guard: old browser bundles only know /rebuild. A persisted
    # checkpoint is authoritative regardless of the legacy status value; never
    # let an old button accidentally clear partial graph data.
    checkpoint = db.graph_checkpoint_stats(kb_id)
    if checkpoint["total"]:
        if knowledge_base["graph_status"] != "paused":
            db.pause_graph_build(kb_id, "检测到未完成的构建检查点，可从当前进度继续")
            knowledge_base = require_knowledge_base(kb_id)
        return resume_graph_from_checkpoint(kb_id, knowledge_base)
    total = int(knowledge_base["chunk_count"])
    if settings.graph_max_chunks:
        total = min(total, settings.graph_max_chunks)
    db.start_graph_build(kb_id, total)
    launch_graph_task(kb_id, resume=False)
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
    if checkpoint["total"] and knowledge_base["graph_status"] != "paused":
        db.pause_graph_build(kb_id, "检测到未完成的构建检查点，可从当前进度继续")
        knowledge_base = require_knowledge_base(kb_id)
    if knowledge_base["graph_status"] != "paused":
        raise HTTPException(status_code=409, detail="当前图谱任务不处于暂停状态")
    return resume_graph_from_checkpoint(kb_id, knowledge_base)


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
        **data,
        "communities": communities,
    }
