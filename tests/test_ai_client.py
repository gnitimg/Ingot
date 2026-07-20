from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services.ai_client import AIClient, AIOutputTruncatedError


@pytest.mark.asyncio
async def test_reranker_parses_scores_and_filters_invalid_indices():
    settings = Settings(
        _env_file=None,
        embedding_api_key="test-key",
        rerank_enabled=True,
        rerank_model="BAAI/bge-reranker-v2-m3",
    )
    client = AIClient(settings)
    client._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "results": [
                {"index": 1, "relevance_score": 0.91},
                {"index": 99, "relevance_score": 1},
            ]
        }
    )

    result = await client.rerank("query", ["first", "second"], 2)

    assert result == [{"index": 1, "score": 0.91}]


@pytest.mark.asyncio
async def test_post_json_reuses_client_and_recovers_from_rate_limit(monkeypatch):
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"message": "rate limited"}},
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as shared_client:
        settings = Settings(_env_file=None, embedding_api_key="test-key")
        client = AIClient(settings, shared_client)
        monkeypatch.setattr(client, "_retry_delay", lambda *_args: 0)

        result = await client._post_json("https://example.test/v1", {}, "test-key", 10)

        assert result == {"ok": True}
        assert attempts == 3
        assert client._http_client() is shared_client


@pytest.mark.asyncio
async def test_chat_complete_forwards_graph_request_controls():
    settings = Settings(
        _env_file=None,
        embedding_api_key="test-key",
        chat_model="Qwen/Qwen3-8B",
    )
    client = AIClient(settings)
    client._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value={"choices": [{"message": {"content": '{"ok":true}'}}]}
    )

    result = await client.chat_complete(
        [{"role": "user", "content": "extract"}],
        json_mode=True,
        enable_thinking=False,
        request_timeout=30,
        max_attempts=2,
    )

    assert result == '{"ok":true}'
    request = client._post_json.await_args
    assert request.args[1]["enable_thinking"] is False
    assert request.args[3] == 30
    assert request.kwargs["max_attempts"] == 2


@pytest.mark.asyncio
async def test_chat_complete_sends_strict_json_schema():
    settings = Settings(
        _env_file=None,
        embedding_api_key="test-key",
        chat_model="Qwen/Qwen3-8B",
    )
    client = AIClient(settings)
    client._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"items":[]}'},
                }
            ]
        }
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "items": {"type": "string"}}},
    }

    result = await client.chat_complete(
        [{"role": "user", "content": "extract"}],
        json_schema=schema,
        schema_name="test_items",
    )

    assert result == '{"items":[]}'
    payload = client._post_json.await_args.args[1]
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "test_items",
            "strict": True,
            "schema": schema,
        },
    }


@pytest.mark.asyncio
async def test_chat_complete_reports_length_truncation():
    settings = Settings(
        _env_file=None,
        embedding_api_key="test-key",
        chat_model="Qwen/Qwen3-8B",
    )
    client = AIClient(settings)
    client._post_json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"entities": ['},
                }
            ]
        }
    )

    with pytest.raises(AIOutputTruncatedError):
        await client.chat_complete([{"role": "user", "content": "extract"}])
