from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def vector_to_blob(vector: Sequence[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    graph_status TEXT NOT NULL DEFAULT 'empty',
    graph_error TEXT,
    graph_stage TEXT NOT NULL DEFAULT '',
    graph_progress_current INTEGER NOT NULL DEFAULT 0,
    graph_progress_total INTEGER NOT NULL DEFAULT 0,
    graph_failed_chunks INTEGER NOT NULL DEFAULT 0,
    graph_started_at TEXT,
    graph_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'processing',
    extraction_method TEXT NOT NULL DEFAULT 'pending',
    page_count INTEGER NOT NULL DEFAULT 0,
    ocr_page_count INTEGER NOT NULL DEFAULT 0,
    warning TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_kb ON documents(kb_id);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_number INTEGER,
    token_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    embedding BLOB NOT NULL,
    embedding_dim INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_kb ON chunks(kb_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE TABLE IF NOT EXISTS graph_build_chunks (
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(kb_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_build_chunks_pending
ON graph_build_chunks(kb_id, status, position);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    entity_type TEXT NOT NULL DEFAULT '概念',
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(kb_id, normalized_name)
);

CREATE INDEX IF NOT EXISTS idx_entities_kb ON entities(kb_id);

CREATE TABLE IF NOT EXISTS entity_chunks (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    mention_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(entity_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_chunks_chunk ON entity_chunks(chunk_id);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    weight REAL NOT NULL DEFAULT 1.0,
    evidence_chunk_id TEXT REFERENCES chunks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE(kb_id, source_id, target_id, predicate)
);

CREATE INDEX IF NOT EXISTS idx_relationships_kb ON relationships(kb_id);
CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id);

CREATE TABLE IF NOT EXISTS communities (
    id TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    embedding BLOB,
    embedding_dim INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_communities_kb ON communities(kb_id);

CREATE TABLE IF NOT EXISTS community_entities (
    community_id TEXT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    PRIMARY KEY(community_id, entity_id)
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            knowledge_base_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(knowledge_bases)").fetchall()
            }
            knowledge_base_migrations = {
                "graph_stage": "TEXT NOT NULL DEFAULT ''",
                "graph_progress_current": "INTEGER NOT NULL DEFAULT 0",
                "graph_progress_total": "INTEGER NOT NULL DEFAULT 0",
                "graph_failed_chunks": "INTEGER NOT NULL DEFAULT 0",
                "graph_started_at": "TEXT",
                "graph_heartbeat_at": "TEXT",
            }
            for column, definition in knowledge_base_migrations.items():
                if column not in knowledge_base_columns:
                    connection.execute(
                        f"ALTER TABLE knowledge_bases ADD COLUMN {column} {definition}"
                    )
            document_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(documents)").fetchall()
            }
            migrations = {
                "extraction_method": "TEXT NOT NULL DEFAULT 'pending'",
                "page_count": "INTEGER NOT NULL DEFAULT 0",
                "ocr_page_count": "INTEGER NOT NULL DEFAULT 0",
                "warning": "TEXT",
            }
            for column, definition in migrations.items():
                if column not in document_columns:
                    connection.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")
            now = utc_now()
            connection.execute(
                """UPDATE knowledge_bases
                   SET graph_status = 'paused',
                       graph_error = '服务重启导致图谱构建暂停，可从当前检查点继续',
                       graph_stage = CASE WHEN graph_stage = '' THEN 'interrupted' ELSE graph_stage END,
                       graph_heartbeat_at = ?, updated_at = ?
                   WHERE graph_status = 'building'""",
                (now, now),
            )

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self.connection() as connection:
            connection.execute(sql, params)

    def create_knowledge_base(self, name: str, description: str) -> dict[str, Any]:
        kb_id = new_id()
        now = utc_now()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO knowledge_bases(id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (kb_id, name, description, now, now),
            )
        return self.get_knowledge_base(kb_id)  # type: ignore[return-value]

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT kb.*,
                      (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id AND d.status = 'ready') AS document_count,
                      (SELECT COUNT(*) FROM chunks c WHERE c.kb_id = kb.id) AS chunk_count,
                      (SELECT COUNT(*) FROM entities e WHERE e.kb_id = kb.id) AS entity_count,
                      (SELECT COUNT(*) FROM relationships r WHERE r.kb_id = kb.id) AS relationship_count
               FROM knowledge_bases kb ORDER BY kb.updated_at DESC"""
        )

    def get_knowledge_base(self, kb_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT kb.*,
                      (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id AND d.status = 'ready') AS document_count,
                      (SELECT COUNT(*) FROM chunks c WHERE c.kb_id = kb.id) AS chunk_count,
                      (SELECT COUNT(*) FROM entities e WHERE e.kb_id = kb.id) AS entity_count,
                      (SELECT COUNT(*) FROM relationships r WHERE r.kb_id = kb.id) AS relationship_count,
                      (SELECT COUNT(*) FROM communities c WHERE c.kb_id = kb.id) AS community_count
               FROM knowledge_bases kb WHERE kb.id = ?""",
            (kb_id,),
        )

    def delete_knowledge_base(self, kb_id: str) -> list[str]:
        paths = [row["stored_path"] for row in self.fetch_all("SELECT stored_path FROM documents WHERE kb_id = ?", (kb_id,))]
        self.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
        return paths

    def touch_knowledge_base(self, kb_id: str) -> None:
        self.execute("UPDATE knowledge_bases SET updated_at = ? WHERE id = ?", (utc_now(), kb_id))

    def set_graph_status(self, kb_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        if status in {"empty", "stale"}:
            self.execute(
                """UPDATE knowledge_bases
                   SET graph_status = ?, graph_error = ?, graph_stage = '',
                       graph_progress_current = 0, graph_progress_total = 0,
                       graph_failed_chunks = 0, graph_started_at = NULL,
                       graph_heartbeat_at = NULL, updated_at = ?
                   WHERE id = ?""",
                (status, error, now, kb_id),
            )
            return
        stage = "completed" if status == "ready" else "failed" if status == "error" else "queued"
        self.execute(
            """UPDATE knowledge_bases
               SET graph_status = ?, graph_error = ?, graph_stage = ?,
                   graph_heartbeat_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, error, stage, now, now, kb_id),
        )

    def start_graph_build(self, kb_id: str, total: int) -> None:
        now = utc_now()
        self.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'building', graph_error = NULL, graph_stage = 'queued',
                   graph_progress_current = 0, graph_progress_total = ?,
                   graph_failed_chunks = 0, graph_started_at = ?,
                   graph_heartbeat_at = ?, updated_at = ?
               WHERE id = ?""",
            (max(0, total), now, now, now, kb_id),
        )

    def prepare_graph_checkpoints(self, kb_id: str, chunk_ids: Sequence[str]) -> None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("DELETE FROM graph_build_chunks WHERE kb_id = ?", (kb_id,))
            connection.executemany(
                """INSERT INTO graph_build_chunks
                   (kb_id, chunk_id, position, status, error, updated_at)
                   VALUES (?, ?, ?, 'pending', NULL, ?)""",
                [
                    (kb_id, chunk_id, position, now)
                    for position, chunk_id in enumerate(chunk_ids)
                ],
            )

    def graph_checkpoint_stats(self, kb_id: str) -> dict[str, int]:
        row = self.fetch_one(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status != 'pending' THEN 1 ELSE 0 END) AS processed,
                      SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                      SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
               FROM graph_build_chunks WHERE kb_id = ?""",
            (kb_id,),
        ) or {}
        return {
            key: int(row.get(key) or 0)
            for key in ("total", "processed", "succeeded", "failed")
        }

    def pending_graph_chunks(self, kb_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT c.id, c.content, c.chunk_index, d.filename
               FROM graph_build_chunks checkpoint
               JOIN chunks c ON c.id = checkpoint.chunk_id
               JOIN documents d ON d.id = c.document_id
               WHERE checkpoint.kb_id = ? AND checkpoint.status = 'pending'
                 AND d.status = 'ready'
               ORDER BY checkpoint.position""",
            (kb_id,),
        )

    def resume_graph_build(self, kb_id: str) -> None:
        now = utc_now()
        self.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'building', graph_error = NULL,
                   graph_stage = CASE WHEN graph_stage = '' THEN 'queued' ELSE graph_stage END,
                   graph_started_at = COALESCE(graph_started_at, ?),
                   graph_heartbeat_at = ?, updated_at = ?
               WHERE id = ?""",
            (now, now, now, kb_id),
        )

    def pause_graph_build(self, kb_id: str, reason: str) -> None:
        now = utc_now()
        self.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'paused', graph_error = ?,
                   graph_stage = CASE WHEN graph_stage = '' THEN 'interrupted' ELSE graph_stage END,
                   graph_heartbeat_at = ?, updated_at = ?
               WHERE id = ?""",
            (reason[:1000], now, now, kb_id),
        )

    def clear_graph_checkpoints(self, kb_id: str) -> None:
        self.execute("DELETE FROM graph_build_chunks WHERE kb_id = ?", (kb_id,))

    def update_graph_progress(
        self,
        kb_id: str,
        *,
        stage: str,
        current: int,
        total: int,
        failed_chunks: int = 0,
    ) -> None:
        now = utc_now()
        self.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'building', graph_error = NULL, graph_stage = ?,
                   graph_progress_current = ?, graph_progress_total = ?,
                   graph_failed_chunks = ?, graph_heartbeat_at = ?, updated_at = ?
               WHERE id = ?""",
            (
                stage,
                max(0, current),
                max(0, total),
                max(0, failed_chunks),
                now,
                now,
                kb_id,
            ),
        )

    def create_document(
        self, kb_id: str, filename: str, stored_path: str, file_type: str, size_bytes: int
    ) -> str:
        document_id = new_id()
        self.execute(
            """INSERT INTO documents
               (id, kb_id, filename, stored_path, file_type, size_bytes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (document_id, kb_id, filename, stored_path, file_type, size_bytes, utc_now()),
        )
        return document_id

    def set_document_status(
        self,
        document_id: str,
        status: str,
        *,
        chunk_count: int = 0,
        error: str | None = None,
        extraction_method: str = "pending",
        page_count: int = 0,
        ocr_page_count: int = 0,
        warning: str | None = None,
    ) -> None:
        self.execute(
            """UPDATE documents
               SET status = ?, chunk_count = ?, error = ?, extraction_method = ?,
                   page_count = ?, ocr_page_count = ?, warning = ?
               WHERE id = ?""",
            (
                status,
                chunk_count,
                error,
                extraction_method,
                page_count,
                ocr_page_count,
                warning,
                document_id,
            ),
        )

    def insert_chunks(self, document_id: str, kb_id: str, chunks: list[dict[str, Any]]) -> None:
        now = utc_now()
        records = [
            (
                chunk["id"],
                document_id,
                kb_id,
                chunk["content"],
                chunk["chunk_index"],
                chunk.get("page_number"),
                chunk.get("token_count", 0),
                json.dumps(chunk.get("metadata", {}), ensure_ascii=False),
                vector_to_blob(chunk["embedding"]),
                len(chunk["embedding"]),
                now,
            )
            for chunk in chunks
        ]
        with self.connection() as connection:
            connection.executemany(
                """INSERT INTO chunks
                   (id, document_id, kb_id, content, chunk_index, page_number, token_count,
                    metadata_json, embedding, embedding_dim, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                records,
            )

    def list_documents(self, kb_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT id, kb_id, filename, file_type, size_bytes, chunk_count, status,
                      extraction_method, page_count, ocr_page_count, warning, error, created_at
               FROM documents WHERE kb_id = ? ORDER BY created_at DESC""",
            (kb_id,),
        )

    def delete_document(self, kb_id: str, document_id: str) -> str | None:
        row = self.fetch_one(
            "SELECT stored_path FROM documents WHERE id = ? AND kb_id = ?", (document_id, kb_id)
        )
        if not row:
            return None
        self.execute("DELETE FROM documents WHERE id = ? AND kb_id = ?", (document_id, kb_id))
        self.invalidate_graph(kb_id, "文档已删除，原图谱已清空；下次构建将从零开始")
        return str(row["stored_path"])

    def chunks_for_search(self, kb_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT c.id, c.document_id, c.content, c.chunk_index, c.page_number,
                      c.metadata_json, c.embedding, c.embedding_dim, d.filename
               FROM chunks c JOIN documents d ON d.id = c.document_id
               WHERE c.kb_id = ? AND d.status = 'ready'""",
            (kb_id,),
        )

    def chunks_for_graph(self, kb_id: str, limit: int = 0) -> list[dict[str, Any]]:
        sql = """SELECT c.id, c.content, c.chunk_index, d.filename
                 FROM chunks c JOIN documents d ON d.id = c.document_id
                 WHERE c.kb_id = ? AND d.status = 'ready'
                 ORDER BY d.created_at, c.chunk_index"""
        params: list[Any] = [kb_id]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.fetch_all(sql, params)

    def get_chunks(self, chunk_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = self.fetch_all(
            f"""SELECT c.id, c.document_id, c.content, c.chunk_index, c.page_number,
                       c.metadata_json, d.filename
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.id IN ({placeholders})""",
            list(chunk_ids),
        )
        order = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        rows.sort(key=lambda row: order.get(row["id"], len(order)))
        return rows

    def clear_graph(self, kb_id: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM communities WHERE kb_id = ?", (kb_id,))
            connection.execute("DELETE FROM relationships WHERE kb_id = ?", (kb_id,))
            connection.execute("DELETE FROM entities WHERE kb_id = ?", (kb_id,))

    def clear_communities(self, kb_id: str) -> None:
        self.execute("DELETE FROM communities WHERE kb_id = ?", (kb_id,))

    def invalidate_graph(self, kb_id: str, reason: str, *, stage: str = "invalidated") -> None:
        now = utc_now()
        with self.connection() as connection:
            connection.execute("DELETE FROM communities WHERE kb_id = ?", (kb_id,))
            connection.execute("DELETE FROM relationships WHERE kb_id = ?", (kb_id,))
            connection.execute("DELETE FROM entities WHERE kb_id = ?", (kb_id,))
            connection.execute("DELETE FROM graph_build_chunks WHERE kb_id = ?", (kb_id,))
            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE kb_id = ?", (kb_id,)
                ).fetchone()[0]
            )
            connection.execute(
                """UPDATE knowledge_bases
                   SET graph_status = ?, graph_error = ?, graph_stage = ?,
                       graph_progress_current = 0, graph_progress_total = 0,
                       graph_failed_chunks = 0, graph_started_at = NULL,
                       graph_heartbeat_at = NULL, updated_at = ?
                   WHERE id = ?""",
                ("stale" if chunk_count else "empty", reason[:1000], stage, now, kb_id),
            )

    def graph_data(self, kb_id: str, limit: int = 500) -> dict[str, list[dict[str, Any]]]:
        entities = self.fetch_all(
            """SELECT e.id, e.name, e.entity_type AS type, e.description,
                      COUNT(ec.chunk_id) AS mentions
               FROM entities e LEFT JOIN entity_chunks ec ON ec.entity_id = e.id
               WHERE e.kb_id = ? GROUP BY e.id ORDER BY mentions DESC, e.name LIMIT ?""",
            (kb_id, limit),
        )
        ids = [entity["id"] for entity in entities]
        if not ids:
            return {"nodes": [], "edges": []}
        placeholders = ",".join("?" for _ in ids)
        relationships = self.fetch_all(
            f"""SELECT id, source_id AS source, target_id AS target, predicate AS label,
                       description, weight
                FROM relationships
                WHERE kb_id = ? AND source_id IN ({placeholders}) AND target_id IN ({placeholders})
                ORDER BY weight DESC""",
            [kb_id, *ids, *ids],
        )
        return {"nodes": entities, "edges": relationships}
