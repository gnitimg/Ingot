from app.database import Database


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
