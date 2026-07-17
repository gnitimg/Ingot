from __future__ import annotations

import re
import hashlib
import shutil
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.database import Database, new_id
from app.services.ai_client import AIClient
from app.services.chunker import chunk_sections
from app.services.ocr import OCRService
from app.services.parsers import SUPPORTED_EXTENSIONS, parse_document


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "document"
    return re.sub(r"[^\w.\-()\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)[:180]


class IngestionService:
    def __init__(self, db: Database, ai: AIClient, settings: Settings):
        self.db = db
        self.ai = ai
        self.settings = settings
        self.ocr = OCRService(ai, settings)

    async def ingest_upload(self, kb_id: str, upload: UploadFile) -> dict[str, str | int | None]:
        original_name = upload.filename or "document"
        extension = Path(original_name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件类型：{extension or '无扩展名'}")

        kb_dir = self.settings.upload_dir / kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{new_id()}_{safe_filename(original_name)}"
        stored_path = kb_dir / stored_name
        size = 0
        hasher = hashlib.sha256()
        max_size = self.settings.max_upload_mb * 1024 * 1024
        try:
            with stored_path.open("wb") as output:
                while data := await upload.read(1024 * 1024):
                    size += len(data)
                    if size > max_size:
                        raise ValueError(f"文件超过 {self.settings.max_upload_mb} MB 限制")
                    hasher.update(data)
                    output.write(data)
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        document_id = self.db.create_document(
            kb_id, original_name, str(stored_path), extension.lstrip("."), size, hasher.hexdigest()
        )
        try:
            parsed = parse_document(
                stored_path,
                original_name,
                ocr_min_text_chars=self.settings.ocr_min_text_chars,
                ocr_max_pages=self.settings.ocr_max_pages,
                ocr_render_dpi=self.settings.ocr_render_dpi,
            )
            ocr_sections, ocr_warnings = await self.ocr.recognize(parsed.ocr_targets)
            sections = sorted(
                [*parsed.sections, *ocr_sections],
                key=lambda section: section.page_number or 0,
            )
            warnings = [*parsed.warnings, *ocr_warnings]
            if not sections:
                warning = "；".join(warnings)
                raise ValueError(warning or "文件中没有提取到可用文本")
            text_chunks = chunk_sections(sections, self.settings.chunk_size, self.settings.chunk_overlap)
            if not text_chunks:
                raise ValueError("文件没有生成有效文本块")
            embeddings = await self.ai.embed_batched([chunk.content for chunk in text_chunks])
            chunks = [
                {
                    "id": new_id(),
                    "content": chunk.content,
                    "chunk_index": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "token_count": chunk.token_count,
                    "metadata": chunk.metadata,
                    "embedding": embedding,
                }
                for chunk, embedding in zip(text_chunks, embeddings, strict=True)
            ]
            self.db.insert_chunks(document_id, kb_id, chunks)
            ocr_count = sum(
                section.metadata.get("extraction_method") == "ocr" for section in sections
            )
            native_count = len(sections) - ocr_count
            extraction_method = "hybrid" if native_count and ocr_count else "ocr" if ocr_count else "native"
            self.db.set_document_status(
                document_id,
                "ready",
                chunk_count=len(chunks),
                extraction_method=extraction_method,
                page_count=parsed.page_count,
                ocr_page_count=ocr_count,
                warning="；".join(warnings)[:1000] or None,
            )
            self.db.invalidate_graph(
                kb_id,
                "文档发生变更，原图谱已清空；下次构建将从零开始",
            )
            self.db.touch_knowledge_base(kb_id)
            return {
                "id": document_id,
                "filename": original_name,
                "status": "ready",
                "chunk_count": len(chunks),
                "sha256": hasher.hexdigest(),
                "extraction_method": extraction_method,
                "page_count": parsed.page_count,
                "ocr_page_count": ocr_count,
                "warning": "；".join(warnings)[:1000] or None,
                "error": None,
            }
        except Exception as exc:
            self.db.set_document_status(document_id, "error", error=str(exc)[:1000])
            return {
                "id": document_id,
                "filename": original_name,
                "status": "error",
                "chunk_count": 0,
                "sha256": hasher.hexdigest(),
                "error": str(exc),
            }

    @staticmethod
    def remove_file(path: str | None) -> None:
        if not path:
            return
        file_path = Path(path)
        if file_path.exists() and file_path.is_file():
            file_path.unlink()

    @staticmethod
    def remove_tree(path: Path) -> None:
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
