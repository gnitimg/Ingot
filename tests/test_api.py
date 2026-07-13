from fastapi.testclient import TestClient


def test_api_lifecycle_and_static_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMBEDDING_API_KEY", "***")
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main as main

    with TestClient(main.app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "Ingot" in root.text

        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["embedding_configured"] is False

        created = client.post(
            "/api/knowledge-bases", json={"name": "API 测试库", "description": "test"}
        )
        assert created.status_code == 201
        kb_id = created.json()["id"]

        listed = client.get("/api/knowledge-bases").json()
        assert listed[0]["name"] == "API 测试库"
        assert client.delete(f"/api/knowledge-bases/{kb_id}").status_code == 204
        assert client.get("/api/knowledge-bases").json() == []
