from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.ai_client import AIClient


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
