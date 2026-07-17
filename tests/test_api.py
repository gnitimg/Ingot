import asyncio
import time

from fastapi.testclient import TestClient


def test_api_lifecycle_and_static_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMBEDDING_API_KEY", "***")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main as main
    monkeypatch.setattr(main, "ENV_PATH", tmp_path / ".env")

    with TestClient(main.app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "Ingot" in root.text

        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["embedding_configured"] is False

        created = client.post(
            "/api/knowledge-bases",
            json={"name": "API 测试库", "description": "test", "password": "secret"},
        )
        assert created.status_code == 201
        created_payload = created.json()
        kb_id = created_payload["id"]
        auth_headers = {"X-Ingot-KB-Token": created_payload["access_token"]}
        assert "password_hash" not in created.text
        assert client.get(f"/api/knowledge-bases/{kb_id}").status_code == 401
        assert client.post(
            f"/api/knowledge-bases/{kb_id}/unlock", json={"password": "wrong"}
        ).status_code == 401
        unlocked = client.post(
            f"/api/knowledge-bases/{kb_id}/unlock", json={"password": "secret"}
        )
        assert unlocked.status_code == 200
        auth_headers = {"X-Ingot-KB-Token": unlocked.json()["access_token"]}

        listed = client.get("/api/knowledge-bases").json()
        assert listed[0]["name"] == "API 测试库"

        document_id = main.db.create_document(kb_id, "graph.md", "graph.md", "md", 5)
        main.db.insert_chunks(
            document_id,
            kb_id,
            [{"id": "graph-chunk", "content": "graph", "chunk_index": 0, "embedding": [1.0]}],
        )
        main.db.set_document_status(document_id, "ready", chunk_count=1)

        async def slow_graph_build(target_id, *, resume=False):
            assert resume is False
            main.db.start_graph_build(target_id, 1)
            main.db.update_graph_progress(
                target_id, stage="extracting", current=0, total=1
            )
            await asyncio.sleep(60)

        monkeypatch.setattr(main.graph_rag, "rebuild", slow_graph_build)
        started = client.post(f"/api/knowledge-bases/{kb_id}/graph/rebuild", headers=auth_headers)
        assert started.status_code == 202
        assert started.json()["total"] == 1
        building = client.get(f"/api/knowledge-bases/{kb_id}", headers=auth_headers).json()
        assert building["graph_status"] == "building"
        assert building["graph_progress_total"] == 1
        assert client.post(
            f"/api/knowledge-bases/{kb_id}/graph/cancel", headers=auth_headers
        ).status_code == 202
        stopped = client.get(f"/api/knowledge-bases/{kb_id}", headers=auth_headers).json()
        assert stopped["graph_status"] == "stale"
        assert stopped["graph_stage"] == "stopped"
        assert stopped["graph_progress_total"] == 0

        main.db.execute(
            """UPDATE knowledge_bases
               SET graph_status = 'error', graph_error = ?, graph_stage = 'failed',
                   graph_progress_current = 0, graph_progress_total = 1
               WHERE id = ?""",
            ("图谱构建超过 60 分钟上限，已自动停止；可调整超时后重试", kb_id),
        )

        async def finish_resumed_graph(target_id, *, resume=False):
            assert target_id == kb_id
            assert resume is True
            main.db.set_graph_status(target_id, "ready")
            main.db.clear_graph_checkpoints(target_id)

        monkeypatch.setattr(main.graph_rag, "rebuild", finish_resumed_graph)
        # A cached legacy frontend still posts to /rebuild. Once checkpoints
        # exist, the compatibility endpoint must resume instead of clearing them.
        resumed = client.post(f"/api/knowledge-bases/{kb_id}/graph/rebuild", headers=auth_headers)
        assert resumed.status_code == 202
        assert resumed.json()["resumed"] is True
        assert resumed.json()["total"] == 1
        for _ in range(50):
            resumed_state = client.get(f"/api/knowledge-bases/{kb_id}", headers=auth_headers).json()
            if resumed_state["graph_status"] == "ready":
                break
            time.sleep(0.01)
        assert resumed_state["graph_status"] == "ready"

        # The explicit resume endpoint follows the same persisted checkpoint.
        main.db.prepare_graph_checkpoints(kb_id, ["graph-chunk"])
        main.db.pause_graph_build(kb_id, "test pause")
        resumed_explicitly = client.post(
            f"/api/knowledge-bases/{kb_id}/graph/resume", headers=auth_headers
        )
        assert resumed_explicitly.status_code == 202
        assert resumed_explicitly.json()["resumed"] is True
        for _ in range(50):
            resumed_state = client.get(f"/api/knowledge-bases/{kb_id}", headers=auth_headers).json()
            if resumed_state["graph_status"] == "ready":
                break
            time.sleep(0.01)
        assert resumed_state["graph_status"] == "ready"

        changed_password = client.put(
            f"/api/knowledge-bases/{kb_id}/password",
            headers=auth_headers,
            json={"old_password": "secret", "new_password": "new-secret"},
        )
        assert changed_password.status_code == 200
        auth_headers = {"X-Ingot-KB-Token": changed_password.json()["access_token"]}

        assert client.delete(f"/api/knowledge-bases/{kb_id}", headers=auth_headers).status_code == 204
        assert client.get("/api/knowledge-bases").json() == []

        current = client.get("/api/settings").json()
        payload = {
            "embedding": {
                "base_url": current["embedding_base_url"],
                "model": "BAAI/bge-m3-test",
                "api_key": "test-secret-key",
                "batch_size": current["embedding_batch_size"],
                "timeout": current["embedding_timeout"],
            },
            "chat": {
                "base_url": current["chat_base_url"],
                "model": current["chat_model"],
                "api_key": "",
                "use_embedding_provider": True,
                "timeout": current["chat_timeout"],
                "temperature": current["chat_temperature"],
                "max_tokens": current["chat_max_tokens"],
                "evidence_count": current["qa_evidence_count"],
            },
            "ocr": {
                "enabled": current["ocr_enabled"],
                "base_url": current["ocr_base_url"],
                "model": current["ocr_model"],
                "api_key": "",
                "use_embedding_provider": True,
                "timeout": current["ocr_timeout"],
                "concurrency": current["ocr_concurrency"],
                "min_text_chars": current["ocr_min_text_chars"],
                "max_pages": current["ocr_max_pages"],
                "render_dpi": current["ocr_render_dpi"],
            },
            "rerank": {
                "enabled": current["rerank_enabled"],
                "base_url": current["rerank_base_url"],
                "model": current["rerank_model"],
                "api_key": "",
                "use_embedding_provider": True,
                "candidates": current["rerank_candidates"],
                "timeout": current["rerank_timeout"],
            },
            "chunking": {
                "chunk_size": current["chunk_size"],
                "chunk_overlap": current["chunk_overlap"],
                "default_top_k": current["default_top_k"],
            },
            "graph": {
                "concurrency": 1000,
                "max_chunks": current["graph_max_chunks"],
                "chunk_timeout": current["graph_chunk_timeout"],
                "build_timeout": current["graph_build_timeout"],
                "retry_rounds": current["graph_retry_rounds"],
                "retry_backoff": current["graph_retry_backoff"],
            },
        }
        updated = client.put("/api/settings", json=payload)
        assert updated.status_code == 200
        assert updated.json()["embedding_model"] == "BAAI/bge-m3-test"
        assert updated.json()["graph_concurrency"] == 1000
        assert updated.json()["qa_evidence_count"] == current["qa_evidence_count"]
        assert "test-secret-key" not in updated.text
        assert "EMBEDDING_API_KEY=test-secret-key" in (tmp_path / ".env").read_text(encoding="utf-8")
        assert "GRAPH_CONCURRENCY=1000" in (tmp_path / ".env").read_text(encoding="utf-8")
        assert "QA_EVIDENCE_COUNT=" in (tmp_path / ".env").read_text(encoding="utf-8")

        payload["embedding"]["api_key"] = ""
        retained = client.put("/api/settings", json=payload)
        assert retained.status_code == 200
        assert "EMBEDDING_API_KEY=test-secret-key" in (tmp_path / ".env").read_text(encoding="utf-8")

        payload["graph"]["concurrency"] = 1001
        rejected_concurrency = client.put("/api/settings", json=payload)
        assert rejected_concurrency.status_code == 422

        payload["graph"]["concurrency"] = 1000
        payload["embedding"]["api_key"] = "${SHOULD_NOT_LEAK}"
        rejected = client.put("/api/settings", json=payload)
        assert rejected.status_code == 422
        assert "SHOULD_NOT_LEAK" not in rejected.text
