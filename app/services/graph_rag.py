from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from typing import Any

from app.config import Settings
from app.database import Database, new_id, utc_now, vector_to_blob
from app.services.ai_client import AIClient, AIServiceError


GRAPH_SYSTEM_PROMPT = """你是严谨的知识图谱工程师。请从文本中抽取对问答有价值的实体与明确关系。
只输出 JSON 对象，格式如下：
{
  "entities": [{"name": "实体名", "type": "人物/组织/地点/产品/技术/事件/概念/指标/其他", "description": "该实体在本文中的简短定义"}],
  "relationships": [{"source": "源实体", "target": "目标实体", "predicate": "简洁关系", "description": "有事实依据的关系说明", "weight": 1.0}]
}
要求：实体名保持原文语言；同一实体使用一致名称；不得推测文本没有表达的事实；没有内容时返回空数组。"""


def normalize_entity_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return value


class GraphRAGService:
    def __init__(self, db: Database, ai: AIClient, settings: Settings):
        self.db = db
        self.ai = ai
        self.settings = settings

    async def _extract_chunk(self, chunk: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
        prompt = f"文件：{chunk['filename']}\n文本块：{chunk['chunk_index'] + 1}\n\n{chunk['content'][:6000]}"
        async with semaphore:
            try:
                response = await self.ai.chat_complete(
                    [
                        {"role": "system", "content": GRAPH_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    max_tokens=1600,
                    json_mode=True,
                )
            except AIServiceError:
                response = await self.ai.chat_complete(
                    [
                        {"role": "system", "content": GRAPH_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    max_tokens=1600,
                    json_mode=False,
                )
        result = parse_json_object(response)
        result["chunk_id"] = chunk["id"]
        return result

    def _upsert_entity(
        self,
        connection: Any,
        kb_id: str,
        name: str,
        entity_type: str = "概念",
        description: str = "",
    ) -> str | None:
        display_name = re.sub(r"\s+", " ", str(name).strip())[:160]
        normalized = normalize_entity_name(display_name)
        if not normalized:
            return None
        entity_id = new_id()
        connection.execute(
            """INSERT INTO entities
               (id, kb_id, name, normalized_name, entity_type, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(kb_id, normalized_name) DO UPDATE SET
                   description = CASE WHEN length(excluded.description) > length(entities.description)
                                      THEN excluded.description ELSE entities.description END,
                   entity_type = CASE WHEN entities.entity_type = '概念'
                                      THEN excluded.entity_type ELSE entities.entity_type END""",
            (
                entity_id,
                kb_id,
                display_name,
                normalized,
                str(entity_type or "概念")[:40],
                str(description or "")[:600],
                utc_now(),
            ),
        )
        row = connection.execute(
            "SELECT id FROM entities WHERE kb_id = ? AND normalized_name = ?", (kb_id, normalized)
        ).fetchone()
        return str(row["id"]) if row else None

    def _store_extractions(self, kb_id: str, extractions: list[dict[str, Any]]) -> None:
        with self.db.connection() as connection:
            for extraction in extractions:
                chunk_id = extraction["chunk_id"]
                name_to_id: dict[str, str] = {}
                for raw_entity in extraction.get("entities", []):
                    if not isinstance(raw_entity, dict):
                        continue
                    name = str(raw_entity.get("name", ""))
                    entity_id = self._upsert_entity(
                        connection,
                        kb_id,
                        name,
                        str(raw_entity.get("type", "概念")),
                        str(raw_entity.get("description", "")),
                    )
                    if entity_id:
                        name_to_id[normalize_entity_name(name)] = entity_id
                        connection.execute(
                            """INSERT INTO entity_chunks(entity_id, chunk_id, mention_count)
                               VALUES (?, ?, 1)
                               ON CONFLICT(entity_id, chunk_id) DO UPDATE SET
                               mention_count = entity_chunks.mention_count + 1""",
                            (entity_id, chunk_id),
                        )

                for raw_relation in extraction.get("relationships", []):
                    if not isinstance(raw_relation, dict):
                        continue
                    source_name = str(raw_relation.get("source", "")).strip()
                    target_name = str(raw_relation.get("target", "")).strip()
                    predicate = re.sub(r"\s+", " ", str(raw_relation.get("predicate", "相关"))).strip()[:100]
                    if not source_name or not target_name or not predicate:
                        continue
                    source_id = name_to_id.get(normalize_entity_name(source_name)) or self._upsert_entity(
                        connection, kb_id, source_name
                    )
                    target_id = name_to_id.get(normalize_entity_name(target_name)) or self._upsert_entity(
                        connection, kb_id, target_name
                    )
                    if not source_id or not target_id or source_id == target_id:
                        continue
                    try:
                        weight = min(10.0, max(0.1, float(raw_relation.get("weight", 1.0))))
                    except (TypeError, ValueError):
                        weight = 1.0
                    connection.execute(
                        """INSERT INTO relationships
                           (id, kb_id, source_id, target_id, predicate, description, weight,
                            evidence_chunk_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(kb_id, source_id, target_id, predicate) DO UPDATE SET
                               weight = MAX(relationships.weight, excluded.weight),
                               description = CASE WHEN length(excluded.description) > length(relationships.description)
                                                  THEN excluded.description ELSE relationships.description END""",
                        (
                            new_id(),
                            kb_id,
                            source_id,
                            target_id,
                            predicate,
                            str(raw_relation.get("description", ""))[:800],
                            weight,
                            chunk_id,
                            utc_now(),
                        ),
                    )

    @staticmethod
    def _detect_communities(entity_ids: list[str], relations: list[dict[str, Any]]) -> list[list[str]]:
        adjacency: dict[str, dict[str, float]] = {entity_id: {} for entity_id in entity_ids}
        for relation in relations:
            source, target = relation["source_id"], relation["target_id"]
            weight = float(relation.get("weight", 1.0))
            adjacency.setdefault(source, {})[target] = adjacency.setdefault(source, {}).get(target, 0) + weight
            adjacency.setdefault(target, {})[source] = adjacency.setdefault(target, {}).get(source, 0) + weight

        labels = {entity_id: entity_id for entity_id in entity_ids}
        ordered = sorted(entity_ids, key=lambda item: (-len(adjacency.get(item, {})), item))
        for _ in range(12):
            changed = False
            for entity_id in ordered:
                scores: dict[str, float] = defaultdict(float)
                for neighbor, weight in adjacency.get(entity_id, {}).items():
                    scores[labels[neighbor]] += weight
                if not scores:
                    continue
                best = min(scores, key=lambda label: (-scores[label], label))
                if best != labels[entity_id]:
                    labels[entity_id] = best
                    changed = True
            if not changed:
                break

        grouped: dict[str, list[str]] = defaultdict(list)
        for entity_id, label in labels.items():
            grouped[label].append(entity_id)
        return sorted(grouped.values(), key=lambda group: (-len(group), group[0]))

    async def _summarize_community(
        self,
        members: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> tuple[str, str]:
        member_map = {member["id"]: member for member in members}
        entity_lines = [
            f"- {member['name']}（{member['entity_type']}）：{member['description']}" for member in members[:60]
        ]
        relation_lines = [
            f"- {member_map[rel['source_id']]['name']} —{rel['predicate']}→ {member_map[rel['target_id']]['name']}：{rel['description']}"
            for rel in relations[:100]
            if rel["source_id"] in member_map and rel["target_id"] in member_map
        ]
        fallback_title = "、".join(member["name"] for member in members[:3]) or "知识主题"
        fallback_summary = "；".join(relation_lines[:6] or entity_lines[:6])
        if not relation_lines:
            return fallback_title[:80], fallback_summary[:2000]
        prompt = """请把下面的知识图谱社区归纳为一个主题。只输出 JSON：
{"title":"不超过20字的主题名","summary":"150-300字的事实性摘要，覆盖关键实体、关系和结论"}
不要添加输入中不存在的信息。

实体：
%s

关系：
%s""" % ("\n".join(entity_lines), "\n".join(relation_lines))
        try:
            response = await self.ai.chat_complete(
                [{"role": "user", "content": prompt}], temperature=0, max_tokens=700, json_mode=True
            )
            parsed = parse_json_object(response)
            title = str(parsed.get("title") or fallback_title)[:80]
            summary = str(parsed.get("summary") or fallback_summary)[:2000]
            return title, summary
        except Exception:
            return fallback_title[:80], fallback_summary[:2000]

    async def _build_communities(self, kb_id: str, failed_chunks: int) -> None:
        entities = self.db.fetch_all(
            "SELECT id, name, entity_type, description FROM entities WHERE kb_id = ?", (kb_id,)
        )
        relations = self.db.fetch_all(
            """SELECT source_id, target_id, predicate, description, weight
               FROM relationships WHERE kb_id = ?""",
            (kb_id,),
        )
        if not entities:
            self.db.update_graph_progress(
                kb_id,
                stage="communities",
                current=0,
                total=0,
                failed_chunks=failed_chunks,
            )
            return
        communities = self._detect_communities([entity["id"] for entity in entities], relations)
        communities = communities[:100]
        self.db.update_graph_progress(
            kb_id,
            stage="communities",
            current=0,
            total=len(communities),
            failed_chunks=failed_chunks,
        )
        entity_map = {entity["id"]: entity for entity in entities}
        summaries: list[tuple[list[str], str, str]] = []
        for index, member_ids in enumerate(communities, start=1):
            members = [entity_map[entity_id] for entity_id in member_ids]
            member_set = set(member_ids)
            internal_relations = [
                relation
                for relation in relations
                if relation["source_id"] in member_set and relation["target_id"] in member_set
            ]
            title, summary = await self._summarize_community(members, internal_relations)
            summaries.append((member_ids, title, summary))
            self.db.update_graph_progress(
                kb_id,
                stage="communities",
                current=index,
                total=len(communities),
                failed_chunks=failed_chunks,
            )

        self.db.update_graph_progress(
            kb_id,
            stage="embedding",
            current=0,
            total=len(summaries),
            failed_chunks=failed_chunks,
        )
        embeddings = await self.ai.embed_batched([f"{title}\n{summary}" for _, title, summary in summaries])
        with self.db.connection() as connection:
            for (member_ids, title, summary), embedding in zip(
                summaries, embeddings, strict=True
            ):
                community_id = new_id()
                connection.execute(
                    """INSERT INTO communities
                       (id, kb_id, title, summary, member_count, embedding, embedding_dim, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        community_id,
                        kb_id,
                        title,
                        summary,
                        len(member_ids),
                        vector_to_blob(embedding),
                        len(embedding),
                        utc_now(),
                    ),
                )
                connection.executemany(
                    "INSERT INTO community_entities(community_id, entity_id) VALUES (?, ?)",
                    [(community_id, entity_id) for entity_id in member_ids],
                )
        self.db.update_graph_progress(
            kb_id,
            stage="embedding",
            current=len(summaries),
            total=len(summaries),
            failed_chunks=failed_chunks,
        )

    async def _rebuild_pipeline(self, kb_id: str, chunks: list[dict[str, Any]]) -> None:
        self.db.clear_graph(kb_id)
        semaphore = asyncio.Semaphore(self.settings.graph_concurrency)
        successful_extractions = 0
        failed_chunks = 0
        last_error: BaseException | None = None
        batch_size = max(1, self.settings.graph_concurrency)
        self.db.update_graph_progress(
            kb_id, stage="extracting", current=0, total=len(chunks), failed_chunks=0
        )
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(
                        self._extract_chunk(chunk, semaphore),
                        timeout=self.settings.graph_chunk_timeout,
                    )
                    for chunk in batch
                ),
                return_exceptions=True,
            )
            extractions: list[dict[str, Any]] = []
            for chunk, result in zip(batch, results, strict=True):
                if isinstance(result, BaseException):
                    last_error = result
                    failed_chunks += 1
                    extractions.append(
                        {"chunk_id": chunk["id"], "entities": [], "relationships": []}
                    )
                else:
                    successful_extractions += 1
                    extractions.append(result)
            self._store_extractions(kb_id, extractions)
            self.db.update_graph_progress(
                kb_id,
                stage="extracting",
                current=min(start + len(batch), len(chunks)),
                total=len(chunks),
                failed_chunks=failed_chunks,
            )
        if successful_extractions == 0:
            raise RuntimeError(
                f"所有图谱抽取请求均失败：{last_error or '模型未返回有效 JSON'}"
            )
        await self._build_communities(kb_id, failed_chunks)

    async def rebuild(self, kb_id: str) -> None:
        chunks = self.db.chunks_for_graph(kb_id, self.settings.graph_max_chunks)
        self.db.start_graph_build(kb_id, len(chunks))
        try:
            if not chunks:
                raise ValueError("知识库中没有可用于建图的文本块")
            await asyncio.wait_for(
                self._rebuild_pipeline(kb_id, chunks),
                timeout=self.settings.graph_build_timeout,
            )
            self.db.set_graph_status(kb_id, "ready")
        except asyncio.CancelledError:
            self.db.set_graph_status(kb_id, "error", "图谱构建已停止，可重新发起构建")
            raise
        except TimeoutError:
            timeout_minutes = max(1, round(self.settings.graph_build_timeout / 60))
            self.db.set_graph_status(
                kb_id,
                "error",
                f"图谱构建超过 {timeout_minutes} 分钟上限，已自动停止；可调整超时或限制文本块后重试",
            )
        except Exception as exc:
            self.db.set_graph_status(kb_id, "error", str(exc)[:1000])
