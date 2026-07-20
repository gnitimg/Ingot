from pathlib import Path
import sqlite3

from app.database import Database, new_id


def test_database_cascade_and_counts(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    source = tmp_path / "hello.txt"
    source.write_text("hello world", encoding="utf-8")
    kb = db.create_knowledge_base("测试库", "说明", "secret")
    document_id = db.create_document(
        kb["id"], "hello.txt", str(source), "txt", source.stat().st_size, "known-hash"
    )
    db.insert_chunks(
        document_id,
        kb["id"],
        [{
            "id": new_id(), "content": "hello world", "chunk_index": 0, "page_number": None,
            "token_count": 2, "metadata": {}, "embedding": [0.6, 0.8],
        }],
    )
    db.set_document_status(document_id, "ready", chunk_count=1)

    result = db.get_knowledge_base(kb["id"])
    assert result is not None
    assert result["document_count"] == 1
    assert result["chunk_count"] == 1
    assert result["has_password"] == 1
    assert "password_hash" not in result
    assert db.verify_knowledge_base_password(kb["id"], "secret") is True
    assert db.verify_knowledge_base_password(kb["id"], "wrong") is False
    assert db.change_knowledge_base_password(kb["id"], "secret", "new-secret") is True
    assert db.verify_knowledge_base_password(kb["id"], "new-secret") is True
    document = db.list_documents(kb["id"])[0]
    assert document["sha256"] == "known-hash"
    assert document["extraction_method"] == "pending"
    assert document["ocr_page_count"] == 0

    db.delete_knowledge_base(kb["id"])
    assert db.fetch_one("SELECT id FROM documents WHERE id = ?", (document_id,)) is None


def test_graph_data_returns_only_selected_nodes(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    kb = db.create_knowledge_base("图谱", "")
    with db.connection() as connection:
        connection.execute(
            "INSERT INTO entities(id, kb_id, name, normalized_name, entity_type, description, created_at) VALUES ('a', ?, 'A', 'a', '概念', '', 'now')",
            (kb["id"],),
        )
        connection.execute(
            "INSERT INTO entities(id, kb_id, name, normalized_name, entity_type, description, created_at) VALUES ('b', ?, 'B', 'b', '概念', '', 'now')",
            (kb["id"],),
        )
        connection.execute(
            """INSERT INTO relationships(id, kb_id, source_id, target_id, predicate, created_at)
               VALUES ('r', ?, 'a', 'b', '关联', 'now')""",
            (kb["id"],),
        )
    data = db.graph_data(kb["id"])
    assert {node["id"] for node in data["nodes"]} == {"a", "b"}
    assert data["edges"][0]["label"] == "关联"


def test_initialize_migrates_legacy_document_columns(tmp_path: Path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE documents (
               id TEXT PRIMARY KEY, kb_id TEXT, filename TEXT, stored_path TEXT,
               file_type TEXT, size_bytes INTEGER, chunk_count INTEGER,
               status TEXT, error TEXT, created_at TEXT
           )"""
    )
    connection.commit()
    connection.close()

    Database(path).initialize()

    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    connection.close()
    assert {"sha256", "extraction_method", "page_count", "ocr_page_count", "warning"} <= columns


def test_initialize_backfills_document_sha256(tmp_path: Path):
    path = tmp_path / "legacy-hash.db"
    source = tmp_path / "source.txt"
    source.write_text("same content", encoding="utf-8")
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE knowledge_bases (
               id TEXT PRIMARY KEY, name TEXT, description TEXT,
               graph_status TEXT DEFAULT 'empty', graph_error TEXT,
               created_at TEXT, updated_at TEXT
           )"""
    )
    connection.execute(
        """CREATE TABLE documents (
               id TEXT PRIMARY KEY, kb_id TEXT, filename TEXT, stored_path TEXT,
               file_type TEXT, size_bytes INTEGER, chunk_count INTEGER,
               status TEXT, error TEXT, created_at TEXT
           )"""
    )
    connection.execute(
        "INSERT INTO documents VALUES ('doc', 'kb', 'source.txt', ?, 'txt', 12, 0, 'ready', NULL, 'now')",
        (str(source),),
    )
    connection.commit()
    connection.close()

    db = Database(path)
    db.initialize()

    document = db.fetch_one("SELECT sha256 FROM documents WHERE id = 'doc'")
    assert document is not None
    assert len(document["sha256"]) == 64
