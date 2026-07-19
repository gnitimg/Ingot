import asyncio

from app.config import Settings
from app.database import Database, new_id
from app.services.ai_client import AIOutputTruncatedError, AIServiceError
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


class SelectiveFailureAI(FakeGraphAI):
    async def chat_complete(self, messages, **kwargs):
        if len(messages) > 1 and "测试文本 9" in messages[-1]["content"]:
            raise AIServiceError("invalid JSON", retryable=True)
        return await super().chat_complete(messages, **kwargs)


class TruncatedOnceAI(FakeGraphAI):
    def __init__(self):
        self.extraction_calls = 0

    async def chat_complete(self, messages, **kwargs):
        if len(messages) > 1:
            self.extraction_calls += 1
            if self.extraction_calls == 1:
                raise AIOutputTruncatedError()
        return await super().chat_complete(messages, **kwargs)


class EntityMatchingAI(FakeGraphAI):
    async def chat_complete(self, messages, **kwargs):
        if len(messages) == 1 and "pair_id" in messages[0]["content"]:
            return '{"matches":[{"pair_id":0,"same_entity":true,"canonical":"left"}]}'
        return await super().chat_complete(messages, **kwargs)


class CountingCommunityAI(FakeGraphAI):
    def __init__(self):
        self.community_calls = 0

    async def chat_complete(self, messages, **kwargs):
        if len(messages) == 1:
            self.community_calls += 1
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


def test_threshold_builds_partial_graph_and_resume_completes_it(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=10)
    settings.graph_retry_rounds = 0
    settings.graph_success_threshold = 90
    service = GraphRAGService(database, SelectiveFailureAI(), settings)

    asyncio.run(service.rebuild(kb_id))

    partial = database.get_knowledge_base(kb_id)
    assert partial is not None
    assert partial["graph_status"] == "partial"
    assert partial["graph_progress_current"] == 9
    assert partial["graph_progress_total"] == 10
    assert partial["graph_failed_chunks"] == 1
    assert partial["community_count"] == 1
    assert database.graph_checkpoint_stats(kb_id) == {
        "total": 10,
        "processed": 10,
        "succeeded": 9,
        "failed": 1,
    }

    service.ai = FakeGraphAI()
    asyncio.run(service.rebuild(kb_id, resume=True))

    completed = database.get_knowledge_base(kb_id)
    assert completed is not None
    assert completed["graph_status"] == "ready"
    assert completed["graph_failed_chunks"] == 0
    assert database.graph_checkpoint_stats(kb_id)["total"] == 0


def test_below_threshold_pauses_without_building_communities(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=10)
    settings.graph_retry_rounds = 0
    settings.graph_success_threshold = 91
    service = GraphRAGService(database, SelectiveFailureAI(), settings)

    asyncio.run(service.rebuild(kb_id))

    paused = database.get_knowledge_base(kb_id)
    assert paused is not None
    assert paused["graph_status"] == "paused"
    assert paused["graph_progress_current"] == 9
    assert paused["graph_progress_total"] == 10
    assert paused["community_count"] == 0

    settings.graph_success_threshold = 90
    finalized = asyncio.run(service.finalize_partial(kb_id))

    partial = database.get_knowledge_base(kb_id)
    assert finalized is True
    assert partial is not None
    assert partial["graph_status"] == "partial"
    assert partial["graph_failed_chunks"] == 1
    assert partial["community_count"] == 1


def test_truncated_extraction_retries_with_compact_prompt(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=1)
    ai = TruncatedOnceAI()
    service = GraphRAGService(database, ai, settings)

    asyncio.run(service.rebuild(kb_id))

    completed = database.get_knowledge_base(kb_id)
    assert completed is not None
    assert completed["graph_status"] == "ready"
    assert ai.extraction_calls == 2


def test_completed_checkpoint_can_rebuild_interrupted_communities(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=2)
    service = GraphRAGService(database, FakeGraphAI(), settings)
    database.prepare_graph_checkpoints(
        kb_id,
        [chunk["id"] for chunk in database.chunks_for_graph(kb_id)],
    )
    service._store_extractions(
        kb_id,
        [
            {
                "chunk_id": chunk["id"],
                "entities": [
                    {
                        "name": f"实体{index}",
                        "type": "概念",
                        "description": "测试",
                    }
                ],
                "relationships": [],
            }
            for index, chunk in enumerate(database.chunks_for_graph(kb_id))
        ],
    )
    database.pause_graph_build(kb_id, "社区生成被服务重启打断")

    finalized = asyncio.run(service.finalize_partial(kb_id))

    completed = database.get_knowledge_base(kb_id)
    assert finalized is True
    assert completed is not None
    assert completed["graph_status"] == "ready"
    assert completed["community_count"] == 2
    assert database.graph_checkpoint_stats(kb_id)["total"] == 0


def test_completed_community_summaries_are_reused_on_replay(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=2)
    ai = CountingCommunityAI()
    service = GraphRAGService(database, ai, settings)

    asyncio.run(service.rebuild(kb_id))
    first_ids = {
        row["id"]
        for row in database.fetch_all(
            "SELECT id FROM communities WHERE kb_id = ?",
            (kb_id,),
        )
    }
    first_call_count = ai.community_calls

    asyncio.run(service._build_communities(kb_id, 0))
    replayed_ids = {
        row["id"]
        for row in database.fetch_all(
            "SELECT id FROM communities WHERE kb_id = ?",
            (kb_id,),
        )
    }

    assert first_call_count == 1
    assert ai.community_calls == first_call_count
    assert replayed_ids == first_ids


def test_llm_entity_matching_merges_aliases_and_rewires_relationships(tmp_path):
    settings, database, kb_id = graph_fixture(tmp_path, chunk_count=2)
    service = GraphRAGService(database, EntityMatchingAI(), settings)
    chunks = database.chunks_for_graph(kb_id)
    service._store_extractions(
        kb_id,
        [
            {
                "chunk_id": chunks[0]["id"],
                "entities": [
                    {"name": "MES系统", "type": "产品", "description": "制造执行系统"},
                    {"name": "模块甲", "type": "概念", "description": "甲"},
                ],
                "relationships": [
                    {
                        "source": "MES系统",
                        "target": "模块甲",
                        "predicate": "包含",
                        "description": "系统包含模块甲",
                        "weight": 1,
                    }
                ],
            },
            {
                "chunk_id": chunks[1]["id"],
                "entities": [
                    {"name": "MES 系统", "type": "产品", "description": "制造执行系统的正式名称"},
                    {"name": "模块乙", "type": "概念", "description": "乙"},
                ],
                "relationships": [
                    {
                        "source": "MES 系统",
                        "target": "模块乙",
                        "predicate": "包含",
                        "description": "系统包含模块乙",
                        "weight": 1,
                    }
                ],
            },
        ],
    )

    merged = asyncio.run(service._resolve_entity_aliases_with_llm(kb_id))

    entities = database.fetch_all(
        "SELECT id, name FROM entities WHERE kb_id = ?",
        (kb_id,),
    )
    relations = database.fetch_all(
        "SELECT source_id, target_id FROM relationships WHERE kb_id = ?",
        (kb_id,),
    )
    alias_entities = [entity for entity in entities if entity["name"] in {"MES系统", "MES 系统"}]
    assert merged == 1
    assert len(alias_entities) == 1
    alias_id = alias_entities[0]["id"]
    assert len(relations) == 2
    assert {relation["source_id"] for relation in relations} == {alias_id}

