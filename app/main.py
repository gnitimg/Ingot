from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import Database
from app.schemas import ChatRequest, KnowledgeBaseCreate, SearchRequest
from app.services.ai_client import AIClient, AIServiceError
from app.services.graph_rag import GraphRAGService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService


settings = get_settings()
db = Database(settings.database_path)
ai = AIClient(settings)
ingestion = IngestionService(db, ai, settings)
graph_rag = GraphRAGService(db, ai, settings)
retrieval = RetrievalService(db, ai, settings)
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    db.initialize()
    yield


app = FastAPI(
    title="Ingot API",
    description="Local-first RAG + GraphRAG knowledge-base application",
    version="1.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def require_knowledge_base(kb_id: str) -> dict:
    knowledge_base = db.get_knowledge_base(kb_id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return knowledge_base


def model_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


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
        "chat_base_url": settings.effective_chat_base_url,
        "chat_model": settings.chat_model,
        "chat_configured": settings.chat_configured,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_base_url": settings.effective_ocr_base_url,
        "ocr_model": settings.ocr_model,
        "ocr_configured": settings.ocr_configured,
        "ocr_concurrency": settings.ocr_concurrency,
        "ocr_min_text_chars": settings.ocr_min_text_chars,
        "ocr_max_pages": settings.ocr_max_pages,
        "rerank_enabled": settings.rerank_enabled,
        "rerank_base_url": settings.effective_rerank_base_url,
        "rerank_model": settings.rerank_model,
        "rerank_configured": settings.rerank_configured,
        "rerank_candidates": settings.rerank_candidates,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "default_top_k": settings.default_top_k,
        "max_upload_mb": settings.max_upload_mb,
        "graph_max_chunks": settings.graph_max_chunks,
    }


@app.get("/api/knowledge-bases")
async def list_knowledge_bases() -> list[dict]:
    return db.list_knowledge_bases()


@app.post("/api/knowledge-bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(payload: KnowledgeBaseCreate) -> dict:
    return db.create_knowledge_base(payload.name.strip(), payload.description.strip())


@app.get("/api/knowledge-bases/{kb_id}")
async def get_knowledge_base(kb_id: str) -> dict:
    return require_knowledge_base(kb_id)


@app.delete("/api/knowledge-bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb_id: str) -> None:
    require_knowledge_base(kb_id)
    paths = db.delete_knowledge_base(kb_id)
    for path in paths:
        ingestion.remove_file(path)
    ingestion.remove_tree(settings.upload_dir / kb_id)


@app.get("/api/knowledge-bases/{kb_id}/documents")
async def list_documents(kb_id: str) -> list[dict]:
    require_knowledge_base(kb_id)
    return db.list_documents(kb_id)


@app.post("/api/knowledge-bases/{kb_id}/documents")
async def upload_documents(kb_id: str, files: list[UploadFile] = File(...)) -> dict:
    require_knowledge_base(kb_id)
    if not files:
        raise HTTPException(status_code=400, detail="请选择至少一个文件")
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
async def delete_document(kb_id: str, document_id: str) -> None:
    require_knowledge_base(kb_id)
    path = db.delete_document(kb_id, document_id)
    if path is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    ingestion.remove_file(path)


@app.post("/api/knowledge-bases/{kb_id}/search")
async def search(kb_id: str, payload: SearchRequest) -> dict:
    knowledge_base = require_knowledge_base(kb_id)
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="知识库中还没有可检索的文档")
    if payload.mode.startswith("graph") and knowledge_base["graph_status"] != "ready":
        raise HTTPException(status_code=409, detail="图谱尚未构建完成")
    try:
        return await retrieval.search(kb_id, payload.query, payload.mode, payload.top_k)
    except AIServiceError as exc:
        raise model_error(exc) from exc


@app.post("/api/knowledge-bases/{kb_id}/chat")
async def chat(kb_id: str, payload: ChatRequest) -> StreamingResponse:
    knowledge_base = require_knowledge_base(kb_id)
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
async def rebuild_graph(kb_id: str, background_tasks: BackgroundTasks) -> dict:
    knowledge_base = require_knowledge_base(kb_id)
    if knowledge_base["graph_status"] == "building":
        raise HTTPException(status_code=409, detail="图谱正在构建")
    if not knowledge_base["chunk_count"]:
        raise HTTPException(status_code=400, detail="请先上传并解析文档")
    db.set_graph_status(kb_id, "building")
    background_tasks.add_task(graph_rag.rebuild, kb_id)
    return {"status": "building"}


@app.get("/api/knowledge-bases/{kb_id}/graph")
async def get_graph(kb_id: str, limit: int = 500) -> dict:
    knowledge_base = require_knowledge_base(kb_id)
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
