from app.database import Database, new_id


def test_graph_progress_and_interrupted_build_recovery(tmp_path):
    database = Database(tmp_path / "ingot.db")
    database.initialize()
    knowledge_base = database.create_knowledge_base("progress", "")
    kb_id = knowledge_base["id"]

    database.start_graph_build(kb_id, 12)
    building = database.get_knowledge_base(kb_id)
    assert building is not None
    assert building["graph_status"] == "building"
    assert building["graph_stage"] == "queued"
    assert building["graph_progress_current"] == 0
    assert building["graph_progress_total"] == 12
    assert building["graph_started_at"]
    assert building["graph_heartbeat_at"]

    database.update_graph_progress(
        kb_id,
        stage="extracting",
        current=5,
        total=12,
        failed_chunks=1,
    )
    progressed = database.get_knowledge_base(kb_id)
    assert progressed is not None
    assert progressed["graph_stage"] == "extracting"
    assert progressed["graph_progress_current"] == 5
    assert progressed["graph_failed_chunks"] == 1

    database.initialize()
    recovered = database.get_knowledge_base(kb_id)
    assert recovered is not None
    assert recovered["graph_status"] == "paused"
    assert recovered["graph_stage"] == "extracting"
    assert "服务重启" in recovered["graph_error"]


def test_legacy_timeout_recovers_partial_graph_checkpoint(tmp_path):
    database = Database(tmp_path / "ingot.db")
    database.initialize()
    knowledge_base = database.create_knowledge_base("legacy", "")
    kb_id = knowledge_base["id"]
    document_id = database.create_document(kb_id, "legacy.md", "legacy.md", "md", 10)
    chunk_ids = [new_id() for _ in range(3)]
    database.insert_chunks(
        document_id,
        kb_id,
        [
            {"id": chunk_id, "content": f"chunk {index}", "chunk_index": index, "embedding": [1.0]}
            for index, chunk_id in enumerate(chunk_ids)
        ],
    )
    database.set_document_status(document_id, "ready", chunk_count=3)
    entity_id = new_id()
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO entities
               (id, kb_id, name, normalized_name, entity_type, description, created_at)
               VALUES (?, ?, '已完成实体', '已完成实体', '概念', '', datetime('now'))""",
            (entity_id, kb_id),
        )
        connection.execute(
            "INSERT INTO entity_chunks(entity_id, chunk_id, mention_count) VALUES (?, ?, 1)",
            (entity_id, chunk_ids[0]),
        )
        connection.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'error', graph_error = ?, graph_stage = 'failed',
                   graph_progress_current = 1, graph_progress_total = 3
               WHERE id = ?""",
            ("图谱构建超过 60 分钟上限，已自动停止；可调整超时后重试", kb_id),
        )

    database.initialize()

    recovered = database.get_knowledge_base(kb_id)
    checkpoint = database.graph_checkpoint_stats(kb_id)
    assert recovered is not None
    assert recovered["graph_status"] == "paused"
    assert recovered["entity_count"] == 1
    assert checkpoint == {"total": 3, "processed": 1, "succeeded": 1, "failed": 0}
    assert [chunk["id"] for chunk in database.pending_graph_chunks(kb_id)] == chunk_ids[1:]

    database.clear_graph_checkpoints(kb_id)
    database.start_graph_build(kb_id, 3)
    database.initialize()
    interrupted = database.get_knowledge_base(kb_id)
    assert interrupted is not None
    assert interrupted["graph_status"] == "paused"
    assert database.graph_checkpoint_stats(kb_id) == {
        "total": 3,
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
    }
    assert interrupted["graph_progress_current"] == 1
    assert interrupted["graph_progress_total"] == 3


def test_incomplete_legacy_ready_graph_becomes_resumable(tmp_path):
    database = Database(tmp_path / "ingot.db")
    database.initialize()
    knowledge_base = database.create_knowledge_base("incomplete", "")
    kb_id = knowledge_base["id"]
    document_id = database.create_document(kb_id, "partial.md", "partial.md", "md", 10)
    chunk_ids = [new_id() for _ in range(3)]
    database.insert_chunks(
        document_id,
        kb_id,
        [
            {"id": chunk_id, "content": f"chunk {index}", "chunk_index": index, "embedding": [1.0]}
            for index, chunk_id in enumerate(chunk_ids)
        ],
    )
    database.set_document_status(document_id, "ready", chunk_count=3)
    entity_id = new_id()
    with database.connection() as connection:
        connection.execute(
            """INSERT INTO entities
               (id, kb_id, name, normalized_name, entity_type, description, created_at)
               VALUES (?, ?, 'partial', 'partial', '概念', '', datetime('now'))""",
            (entity_id, kb_id),
        )
        connection.execute(
            "INSERT INTO entity_chunks(entity_id, chunk_id, mention_count) VALUES (?, ?, 1)",
            (entity_id, chunk_ids[0]),
        )
        connection.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'ready', graph_stage = 'completed', graph_failed_chunks = 2
               WHERE id = ?""",
            (kb_id,),
        )

    database.initialize()

    recovered = database.get_knowledge_base(kb_id)
    assert recovered is not None
    assert recovered["graph_status"] == "paused"
    assert "2 个失败块" in recovered["graph_error"]
    assert database.graph_checkpoint_stats(kb_id) == {
        "total": 3,
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
    }
    assert [chunk["id"] for chunk in database.pending_graph_chunks(kb_id)] == chunk_ids[1:]
