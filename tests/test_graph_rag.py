import asyncio

from app.config import Settings
from app.database import Database, new_id
from app.services.ai_client import AIServiceError
from app.services.graph_rag import GraphRAGService, normalize_entity_name, parse_json_object


def test_parse_json_from_markdown_fence():
    result = parse_json_object('```json\n{"entities": [], "relationships": []}\n```')
    assert result == {"entities": [], "relationships": []}


def test_normalize_entity_name():
    assert normalize_entity_name("  Silicon   Flow ") == "silicon flow"


def test_detect_communities_separates_components():
    entity_ids = ["a", "b", "c", "d"]
    relations = [
        {"source_id": "a", "target_id": "b", "weight": 1},
        {"source_id": "c", "target_id": "d", "weight": 1},
    ]
    communities = GraphRAGService._detect_communities(entity_ids, relations)
    assert {frozenset(group) for group in communities} == {frozenset({"a", "b"}), frozenset({"c", "d"})}


class FakeGraphAI:
    async def chat_complete(self, messages, **_kwargs):
        if len(messages) == 1:
            return '{"title":"测试主题","summary":"实体甲与实体乙存在明确关系。"}'
        return """{
            "entities": [
                {"name":"实体甲","type":"概念","description":"甲"},
                {"name":"实体乙","type":"概念","description":"乙"}
            ],
            "relationships": [
                {"source":"实体甲","target":"实体乙","predicate":"关联","description":"测试关系","weight":1}
            ]
        }"""

    async def embed_batched(self, texts):
        return [[1.0, 0.0] for _ in texts]


class SlowGraphAI(FakeGraphAI):
    async def chat_complete(self, messages, **kwargs):
        await asyncio.sleep(0.05)
        return await super().chat_complete(messages, **kwargs)


class PauseAfterFirstChunkAI(FakeGraphAI):
    def __init__(self):
        self.extraction_calls = 0

    async def chat_complete(self, messages, **kwargs):
        if len(messages) > 1:
            self.extraction_calls += 1
            if self.extraction_calls > 1:
                await asyncio.sleep(0.2)
        return await super().chat_complete(messages, **kwargs)


class FlakyGraphAI(FakeGraphAI):
    def __init__(self, failures: int):
        self.failures = failures
        self.extraction_calls = 0

    async def chat_complete(self, messages, **kwargs):
        if len(messages) > 1:
            self.extraction_calls += 1
            if self.extraction_calls <= self.failures:
                raise AIServiceError("rate limited", status_code=429, retryable=True)
        return await super().chat_complete(messages, **kwargs)


class SlowFirstChunkAI(FakeGraphAI):
    def __init__(self):
        self.slow_finished = False
        self.third_started_before_slow_finished = False

    async def chat_complete(self, messages, **kwargs):
        if len(messages) > 1:
            prompt = messages[-1]["content"]
            if "测试文本 0" in prompt:
                try:
                    await asyncio.sleep(0.2)
                finally:
                    self.slow_finished = True
            if "测试文本 2" in prompt:
                self.third_started_before_slow_finished = not self.slow_finished
        return await super().chat_complete(messages, **kwargs)


def graph_fixture(tmp_path, chunk_count=2):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_api_key="test",
        chat_api_key="test",
        graph_concurrency=2,
        graph_chunk_timeout=15,
        graph_build_timeout=60,
    )
    database = Database(settings.database_path)
    database.initialize()
    knowledge_base = database.create_knowledge_base("graph", "")
    kb_id = knowledge_base["id"]
    document_id = database.create_document(kb_id, "test.md", "test.md", "md", 10)
    database.insert_chunks(
        document_id,
        kb_id,
        [
            {
                "id": new_id(),
                "content": f"测试文本 {index}",
                "chunk_index": index,
                "embedding": [1.0, 0.0],
            }
            for index in range(chunk_count)
        ],
    )
    database.set_document_status(document_id, "ready", chunk_count=chunk_count)
    return settings, database, kb_id


def test_rebuild_records_progress_and_completes(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path)
    service = GraphRAGService(database, FakeGraphAI(), settings)

    asyncio.run(service.rebuild(kb_id))

    knowledge_base = database.get_knowledge_base(kb_id)
    assert knowledge_base is not None
    assert knowledge_base["graph_status"] == "ready"
    assert knowledge_base["graph_stage"] == "completed"
    assert knowledge_base["graph_failed_chunks"] == 0
    assert knowledge_base["entity_count"] == 2
    assert knowledge_base["relationship_count"] == 1
    assert knowledge_base["community_count"] == 1


def test_rebuild_pauses_when_chunk_retries_are_exhausted(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=1)
    settings.graph_chunk_timeout = 0.01
    settings.graph_retry_rounds = 1
    settings.graph_retry_backoff = 0
    service = GraphRAGService(database, SlowGraphAI(), settings)

    asyncio.run(service.rebuild(kb_id))

    knowledge_base = database.get_knowledge_base(kb_id)
    assert knowledge_base is not None
    assert knowledge_base["graph_status"] == "paused"
    assert knowledge_base["graph_stage"] == "retrying"
    assert knowledge_base["graph_failed_chunks"] == 1
    assert "自动降并发重试" in knowledge_base["graph_error"]
    assert database.graph_checkpoint_stats(kb_id)["failed"] == 1

    service.ai = FakeGraphAI()
    settings.graph_chunk_timeout = 15
    asyncio.run(service.rebuild(kb_id, resume=True))
    completed = database.get_knowledge_base(kb_id)
    assert completed is not None
    assert completed["graph_status"] == "ready"


def test_transient_failure_is_retried_before_graph_is_ready(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=2)
    settings.graph_retry_rounds = 2
    settings.graph_retry_backoff = 0
    ai = FlakyGraphAI(failures=2)
    service = GraphRAGService(database, ai, settings)

    asyncio.run(service.rebuild(kb_id))

    completed = database.get_knowledge_base(kb_id)
    assert completed is not None
    assert completed["graph_status"] == "ready"
    assert completed["graph_failed_chunks"] == 0
    assert ai.extraction_calls == 4


def test_slow_chunk_moves_to_retry_tail_without_blocking_worker_pool(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=3)
    settings.graph_concurrency = 2
    settings.graph_chunk_timeout = 0.05
    settings.graph_retry_rounds = 0
    ai = SlowFirstChunkAI()
    service = GraphRAGService(database, ai, settings)

    asyncio.run(service.rebuild(kb_id))

    checkpoint = database.graph_checkpoint_stats(kb_id)
    assert ai.third_started_before_slow_finished is True
    assert checkpoint["succeeded"] == 2
    assert checkpoint["failed"] == 1


def test_total_timeout_pauses_and_resume_uses_checkpoint(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=2)
    settings.graph_concurrency = 1
    settings.graph_build_timeout = 0.05
    service = GraphRAGService(database, PauseAfterFirstChunkAI(), settings)

    asyncio.run(service.rebuild(kb_id))

    paused = database.get_knowledge_base(kb_id)
    checkpoint = database.graph_checkpoint_stats(kb_id)
    assert paused is not None
    assert paused["graph_status"] == "paused"
    assert paused["graph_failed_chunks"] == 1
    assert checkpoint == {"total": 2, "processed": 1, "succeeded": 1, "failed": 0}
    assert len(database.pending_graph_chunks(kb_id)) == 1

    service.ai = FakeGraphAI()
    settings.graph_build_timeout = 60
    asyncio.run(service.rebuild(kb_id, resume=True))

    completed = database.get_knowledge_base(kb_id)
    assert completed is not None
    assert completed["graph_status"] == "ready"
    assert completed["entity_count"] == 2
    assert database.graph_checkpoint_stats(kb_id)["total"] == 0

