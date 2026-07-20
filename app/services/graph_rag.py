from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import random
import re
from collections import defaultdict
from typing import Any

from app.config import Settings
from app.database import Database, new_id, utc_now, vector_to_blob
from app.services.ai_client import AIClient, AIOutputTruncatedError, AIServiceError


GRAPH_SYSTEM_PROMPT = """你是严谨的知识图谱工程师。请从文本中抽取对问答有价值的实体与明确关系。
只输出 JSON 对象，格式如下：
{
  "entities": [{"name": "实体名", "type": "人物/组织/地点/产品/技术/事件/概念/指标/其他", "description": "该实体在本文中的简短定义"}],
  "relationships": [{"source": "源实体", "target": "目标实体", "predicate": "简洁关系", "description": "有事实依据的关系说明", "weight": 1.0}]
}
要求：实体名保持原文语言；同一实体使用一致名称；不得推测文本没有表达的事实；没有内容时返回空数组。"""


def graph_extraction_schema(max_entities: int = 12, max_relationships: int = 18) -> dict[str, Any]:
    entity = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "type", "description"],
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string"},
            "description": {"type": "string"},
        },
    }
    relationship = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source", "target", "predicate", "description", "weight"],
        "properties": {
            "source": {"type": "string"},
            "target": {"type": "string"},
            "predicate": {"type": "string"},
            "description": {"type": "string"},
            "weight": {"type": "number"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["entities", "relationships"],
        "properties": {
            "entities": {
                "type": "array",
                "maxItems": max_entities,
                "items": entity,
            },
            "relationships": {
                "type": "array",
                "maxItems": max_relationships,
                "items": relationship,
            },
        },
    }


def entity_match_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["matches"],
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["pair_id", "same_entity", "canonical"],
                    "properties": {
                        "pair_id": {"type": "integer"},
                        "same_entity": {"type": "boolean"},
                        "canonical": {
                            "type": "string",
                            "enum": ["left", "right"],
                        },
                    },
                },
            }
        },
    }


def community_summary_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "summary"],
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
        },
    }


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

    def _graph_request_timeout(self) -> float:
        # Keep both HTTP attempts inside the chunk-level deadline. Graph
        # extraction is deterministic JSON work and should not occupy a worker
        # for the much longer interactive-chat timeout.
        return max(
            15.0,
            min(
                self.settings.chat_timeout,
                90.0,
                self.settings.graph_chunk_timeout * 0.4,
            ),
        )

    async def _request_graph_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int,
        max_attempts: int,
    ) -> str:
        try:
            return await self.ai.chat_complete(
                messages,
                temperature=0,
                max_tokens=max_tokens,
                json_mode=True,
                json_schema=schema,
                schema_name=schema_name,
                enable_thinking=False,
                request_timeout=self._graph_request_timeout(),
                max_attempts=max_attempts,
            )
        except AIServiceError as exc:
            if not exc.json_mode_unsupported:
                raise
            return await self.ai.chat_complete(
                messages,
                temperature=0,
                max_tokens=max_tokens,
                json_mode=False,
                enable_thinking=False,
                request_timeout=self._graph_request_timeout(),
                max_attempts=1,
            )

    async def _compact_reextract(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        compact_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "上一次输出过长或无法解析。请重新抽取并显著压缩："
                    "最多 8 个实体、12 条关系，描述不超过 40 字；"
                    "必须返回完整 JSON，不得输出解释。"
                ),
            },
        ]
        response = await self._request_graph_json(
            compact_messages,
            schema=graph_extraction_schema(8, 12),
            schema_name="compact_graph_extraction",
            max_tokens=2600,
            max_attempts=1,
        )
        return parse_json_object(response)

    async def _repair_graph_json(self, response: str) -> dict[str, Any]:
        repair_prompt = """下面是实体关系抽取结果，但 JSON 语法无效。
只修复 JSON 语法和字段结构，不得增加、删除或改写任何事实。
若末尾不完整，只保留已经完整出现的实体和关系；必须返回完整 JSON。

待修复内容：
""" + response[:12000]
        repaired = await self._request_graph_json(
            [{"role": "user", "content": repair_prompt}],
            schema=graph_extraction_schema(),
            schema_name="repaired_graph_extraction",
            max_tokens=3000,
            max_attempts=1,
        )
        return parse_json_object(repaired)

    async def _extract_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        extraction_limits = (
            "\n\n抽取约束：最多返回 12 个实体、18 条关系；"
            "优先保留对问答有价值的核心概念、表名、字段名、角色、流程、指标和系统模块；"
            "实体与关系描述均控制在 80 字以内；不要输出 Markdown、解释文本或额外字段。"
        )
        prompt = (
            f"文件：{chunk['filename']}\n"
            f"文本块：{chunk['chunk_index'] + 1}\n\n"
            f"{chunk['content'][:6000]}"
            f"{extraction_limits}"
        )
        messages = [
            {"role": "system", "content": GRAPH_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            response = await self._request_graph_json(
                messages,
                schema=graph_extraction_schema(),
                schema_name="graph_extraction",
                max_tokens=2400,
                max_attempts=2,
            )
        except AIOutputTruncatedError:
            result = await self._compact_reextract(messages)
        else:
            try:
                result = parse_json_object(response)
            except (json.JSONDecodeError, ValueError):
                try:
                    result = await self._repair_graph_json(response)
                except (AIServiceError, json.JSONDecodeError, ValueError):
                    result = await self._compact_reextract(messages)
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
                connection.execute(
                    """UPDATE graph_build_chunks
                       SET status = ?, error = ?, attempts = attempts + 1, updated_at = ?
                       WHERE kb_id = ? AND chunk_id = ?""",
                    (
                        str(extraction.get("_checkpoint_status", "succeeded")),
                        str(extraction.get("_checkpoint_error") or "")[:1000] or None,
                        utc_now(),
                        kb_id,
                        chunk_id,
                    ),
                )

    @staticmethod
    def _entity_match_candidates(
        entities: list[dict[str, Any]],
        *,
        limit: int = 160,
    ) -> list[tuple[float, dict[str, Any], dict[str, Any]]]:
        candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for index, left in enumerate(entities):
            left_name = normalize_entity_name(str(left["name"]))
            for right in entities[index + 1 :]:
                right_name = normalize_entity_name(str(right["name"]))
                if not left_name or not right_name or left_name == right_name:
                    continue
                left_type = str(left.get("entity_type") or "")
                right_type = str(right.get("entity_type") or "")
                if (
                    left_type
                    and right_type
                    and left_type != right_type
                    and "概念" not in {left_type, right_type}
                    and "其他" not in {left_type, right_type}
                ):
                    continue
                ratio = difflib.SequenceMatcher(None, left_name, right_name).ratio()
                shorter, longer = sorted((left_name, right_name), key=len)
                contained = len(shorter) >= 4 and shorter in longer and ratio >= 0.55
                if ratio >= 0.72 or contained:
                    candidates.append((ratio, left, right))
        candidates.sort(
            key=lambda item: (
                -item[0],
                str(item[1]["name"]),
                str(item[2]["name"]),
            )
        )
        return candidates[:limit]

    def _merge_entity_groups(
        self,
        kb_id: str,
        entities: list[dict[str, Any]],
        groups: list[list[str]],
        preferred_votes: dict[str, int],
    ) -> int:
        entity_map = {str(entity["id"]): entity for entity in entities}
        id_map: dict[str, str] = {}
        for group in groups:
            if len(group) < 2:
                continue
            canonical = max(
                group,
                key=lambda entity_id: (
                    preferred_votes.get(entity_id, 0),
                    int(entity_map[entity_id].get("mention_count") or 0),
                    len(str(entity_map[entity_id].get("description") or "")),
                    -len(str(entity_map[entity_id].get("name") or "")),
                    entity_id,
                ),
            )
            for entity_id in group:
                id_map[entity_id] = canonical
        duplicates = [entity_id for entity_id, target in id_map.items() if entity_id != target]
        if not duplicates:
            return 0

        with self.db.connection() as connection:
            relationships = connection.execute(
                """SELECT id, source_id, target_id, predicate, description, weight,
                          evidence_chunk_id, created_at
                   FROM relationships WHERE kb_id = ?""",
                (kb_id,),
            ).fetchall()
            for duplicate in duplicates:
                canonical = id_map[duplicate]
                connection.execute(
                    """INSERT INTO entity_chunks(entity_id, chunk_id, mention_count)
                       SELECT ?, chunk_id, mention_count
                       FROM entity_chunks WHERE entity_id = ?
                       ON CONFLICT(entity_id, chunk_id) DO UPDATE SET
                           mention_count = entity_chunks.mention_count + excluded.mention_count""",
                    (canonical, duplicate),
                )
                connection.execute(
                    "DELETE FROM entity_chunks WHERE entity_id = ?",
                    (duplicate,),
                )

            merged_relations: dict[tuple[str, str, str], dict[str, Any]] = {}
            for relation in relationships:
                source_id = id_map.get(str(relation["source_id"]), str(relation["source_id"]))
                target_id = id_map.get(str(relation["target_id"]), str(relation["target_id"]))
                if source_id == target_id:
                    continue
                key = (source_id, target_id, str(relation["predicate"]))
                existing = merged_relations.get(key)
                candidate = dict(relation)
                candidate["source_id"] = source_id
                candidate["target_id"] = target_id
                if existing is None:
                    merged_relations[key] = candidate
                    continue
                existing["weight"] = max(
                    float(existing.get("weight") or 1.0),
                    float(candidate.get("weight") or 1.0),
                )
                if len(str(candidate.get("description") or "")) > len(
                    str(existing.get("description") or "")
                ):
                    existing["description"] = candidate.get("description") or ""

            connection.execute("DELETE FROM relationships WHERE kb_id = ?", (kb_id,))
            connection.executemany(
                """INSERT INTO relationships
                   (id, kb_id, source_id, target_id, predicate, description, weight,
                    evidence_chunk_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        new_id(),
                        kb_id,
                        relation["source_id"],
                        relation["target_id"],
                        relation["predicate"],
                        relation.get("description") or "",
                        relation.get("weight") or 1.0,
                        relation.get("evidence_chunk_id"),
                        relation.get("created_at") or utc_now(),
                    )
                    for relation in merged_relations.values()
                ],
            )
            connection.executemany(
                "DELETE FROM entities WHERE id = ?",
                [(entity_id,) for entity_id in duplicates],
            )
        return len(duplicates)

    async def _resolve_entity_aliases_with_llm(self, kb_id: str) -> int:
        entities = self.db.fetch_all(
            """SELECT e.id, e.name, e.entity_type, e.description,
                      COALESCE(SUM(ec.mention_count), 0) AS mention_count
               FROM entities e
               LEFT JOIN entity_chunks ec ON ec.entity_id = e.id
               WHERE e.kb_id = ?
               GROUP BY e.id""",
            (kb_id,),
        )
        candidates = self._entity_match_candidates(entities)
        if not candidates:
            return 0
        stats = self.db.graph_checkpoint_stats(kb_id)
        unresolved = max(0, stats["total"] - stats["succeeded"])
        self.db.update_graph_progress(
            kb_id,
            stage="matching",
            current=0,
            total=len(candidates),
            failed_chunks=unresolved,
        )

        parent = {str(entity["id"]): str(entity["id"]) for entity in entities}
        preferred_votes: dict[str, int] = defaultdict(int)

        def find(entity_id: str) -> str:
            while parent[entity_id] != entity_id:
                parent[entity_id] = parent[parent[entity_id]]
                entity_id = parent[entity_id]
            return entity_id

        def union(left_id: str, right_id: str) -> None:
            left_root, right_root = find(left_id), find(right_id)
            if left_root != right_root:
                parent[right_root] = left_root

        completed = 0
        for start in range(0, len(candidates), 20):
            batch = candidates[start : start + 20]
            prompt_pairs = [
                {
                    "pair_id": start + offset,
                    "left": {
                        "name": left["name"],
                        "type": left["entity_type"],
                        "description": left["description"],
                    },
                    "right": {
                        "name": right["name"],
                        "type": right["entity_type"],
                        "description": right["description"],
                    },
                }
                for offset, (_, left, right) in enumerate(batch)
            ]
            prompt = (
                "判断每一对名称是否指向同一个现实实体或同一个明确概念。"
                "只有证据充分时 same_entity 才能为 true；上下位概念、相关模块、"
                "同类产品不能合并。canonical 选择更完整、正式的名称。\n\n"
                + json.dumps(prompt_pairs, ensure_ascii=False)
            )
            try:
                response = await self._request_graph_json(
                    [{"role": "user", "content": prompt}],
                    schema=entity_match_schema(),
                    schema_name="entity_alias_matches",
                    max_tokens=1400,
                    max_attempts=1,
                )
                parsed = parse_json_object(response)
            except (AIServiceError, json.JSONDecodeError, ValueError):
                completed += len(batch)
                self.db.update_graph_progress(
                    kb_id,
                    stage="matching",
                    current=completed,
                    total=len(candidates),
                    failed_chunks=unresolved,
                )
                continue
            batch_by_id = {
                start + offset: (left, right)
                for offset, (_, left, right) in enumerate(batch)
            }
            for match in parsed.get("matches", []):
                if not isinstance(match, dict) or not match.get("same_entity"):
                    continue
                try:
                    pair_id = int(match.get("pair_id"))
                except (TypeError, ValueError):
                    continue
                pair = batch_by_id.get(pair_id)
                if pair is None:
                    continue
                left, right = pair
                left_id, right_id = str(left["id"]), str(right["id"])
                union(left_id, right_id)
                preferred = left_id if match.get("canonical") == "left" else right_id
                preferred_votes[preferred] += 1
            completed += len(batch)
            self.db.update_graph_progress(
                kb_id,
                stage="matching",
                current=completed,
                total=len(candidates),
                failed_chunks=unresolved,
            )

        grouped: dict[str, list[str]] = defaultdict(list)
        for entity_id in parent:
            grouped[find(entity_id)].append(entity_id)
        return self._merge_entity_groups(
            kb_id,
            entities,
            list(grouped.values()),
            preferred_votes,
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
            response = await asyncio.wait_for(
                self._request_graph_json(
                    [{"role": "user", "content": prompt}],
                    schema=community_summary_schema(),
                    schema_name="community_summary",
                    max_tokens=700,
                    max_attempts=2,
                ),
                timeout=self.settings.graph_chunk_timeout,
            )
            parsed = parse_json_object(response)
            title = str(parsed.get("title") or fallback_title)[:80]
            summary = str(parsed.get("summary") or fallback_summary)[:2000]
            return title, summary
        except Exception:
            return fallback_title[:80], fallback_summary[:2000]

    @staticmethod
    def _community_id(
        kb_id: str,
        members: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> str:
        signature = {
            "members": sorted(
                (
                    str(member["id"]),
                    str(member.get("name") or ""),
                    str(member.get("entity_type") or ""),
                    str(member.get("description") or ""),
                )
                for member in members
            ),
            "relations": sorted(
                (
                    str(relation.get("source_id") or ""),
                    str(relation.get("target_id") or ""),
                    str(relation.get("predicate") or ""),
                    str(relation.get("description") or ""),
                    float(relation.get("weight") or 1.0),
                )
                for relation in relations
            ),
        }
        digest = hashlib.sha256(
            json.dumps(
                signature,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return f"community-{kb_id[:8]}-{digest[:24]}"

    def _store_community_summary(
        self,
        kb_id: str,
        community_id: str,
        member_ids: list[str],
        title: str,
        summary: str,
    ) -> None:
        with self.db.connection() as connection:
            connection.execute(
                """INSERT INTO communities
                   (id, kb_id, title, summary, member_count, embedding,
                    embedding_dim, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, 0, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       title = excluded.title,
                       summary = excluded.summary,
                       member_count = excluded.member_count,
                       embedding = NULL,
                       embedding_dim = 0""",
                (
                    community_id,
                    kb_id,
                    title,
                    summary,
                    len(member_ids),
                    utc_now(),
                ),
            )
            connection.execute(
                "DELETE FROM community_entities WHERE community_id = ?",
                (community_id,),
            )
            connection.executemany(
                """INSERT INTO community_entities(community_id, entity_id)
                   VALUES (?, ?)""",
                [(community_id, entity_id) for entity_id in member_ids],
            )

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
            self.db.clear_communities(kb_id)
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
        entity_map = {entity["id"]: entity for entity in entities}
        jobs: list[
            tuple[str, list[str], list[dict[str, Any]], list[dict[str, Any]]]
        ] = []
        for member_ids in communities:
            member_ids = sorted(member_ids)
            members = [entity_map[entity_id] for entity_id in member_ids]
            member_set = set(member_ids)
            internal_relations = [
                relation
                for relation in relations
                if relation["source_id"] in member_set and relation["target_id"] in member_set
            ]
            jobs.append(
                (
                    self._community_id(kb_id, members, internal_relations),
                    member_ids,
                    members,
                    internal_relations,
                )
            )

        valid_ids = [community_id for community_id, *_ in jobs]
        with self.db.connection() as connection:
            if valid_ids:
                placeholders = ",".join("?" for _ in valid_ids)
                connection.execute(
                    f"""DELETE FROM communities
                        WHERE kb_id = ? AND id NOT IN ({placeholders})""",
                    [kb_id, *valid_ids],
                )
            else:
                connection.execute(
                    "DELETE FROM communities WHERE kb_id = ?",
                    (kb_id,),
                )
        existing_ids = {
            str(row["id"])
            for row in self.db.fetch_all(
                """SELECT id FROM communities
                   WHERE kb_id = ? AND length(summary) > 0""",
                (kb_id,),
            )
        }
        self.db.update_graph_progress(
            kb_id,
            stage="communities",
            current=len(existing_ids),
            total=len(jobs),
            failed_chunks=failed_chunks,
        )
        semaphore = asyncio.Semaphore(max(1, self.settings.graph_concurrency))

        async def summarize_job(
            community_id: str,
            member_ids: list[str],
            members: list[dict[str, Any]],
            internal_relations: list[dict[str, Any]],
        ) -> tuple[str, list[str], str, str]:
            async with semaphore:
                title, summary = await self._summarize_community(members, internal_relations)
            return community_id, member_ids, title, summary

        summary_tasks = [
            asyncio.create_task(
                summarize_job(
                    community_id,
                    member_ids,
                    members,
                    internal_relations,
                )
            )
            for community_id, member_ids, members, internal_relations in jobs
            if community_id not in existing_ids
        ]
        completed_summaries = len(existing_ids)
        try:
            for completed_task in asyncio.as_completed(summary_tasks):
                community_id, member_ids, title, summary = await completed_task
                self._store_community_summary(
                    kb_id,
                    community_id,
                    member_ids,
                    title,
                    summary,
                )
                completed_summaries += 1
                self.db.update_graph_progress(
                    kb_id,
                    stage="communities",
                    current=completed_summaries,
                    total=len(jobs),
                    failed_chunks=failed_chunks,
                )
        finally:
            for task in summary_tasks:
                if not task.done():
                    task.cancel()
            if summary_tasks:
                await asyncio.gather(*summary_tasks, return_exceptions=True)

        pending_embeddings = self.db.fetch_all(
            """SELECT id, title, summary FROM communities
               WHERE kb_id = ? AND embedding_dim = 0
               ORDER BY id""",
            (kb_id,),
        )
        embedded_count = len(jobs) - len(pending_embeddings)
        self.db.update_graph_progress(
            kb_id,
            stage="embedding",
            current=embedded_count,
            total=len(jobs),
            failed_chunks=failed_chunks,
        )
        if pending_embeddings:
            embeddings = await self.ai.embed_batched(
                [
                    f"{community['title']}\n{community['summary']}"
                    for community in pending_embeddings
                ]
            )
            with self.db.connection() as connection:
                connection.executemany(
                    """UPDATE communities
                       SET embedding = ?, embedding_dim = ?
                       WHERE id = ? AND kb_id = ?""",
                    [
                        (
                            vector_to_blob(embedding),
                            len(embedding),
                            community["id"],
                            kb_id,
                        )
                        for community, embedding in zip(
                            pending_embeddings,
                            embeddings,
                            strict=True,
                        )
                    ],
                )
        self.db.update_graph_progress(
            kb_id,
            stage="embedding",
            current=len(jobs),
            total=len(jobs),
            failed_chunks=failed_chunks,
        )

    async def _finalize_graph(self, kb_id: str, failed_chunks: int) -> None:
        if self.settings.graph_llm_entity_matching:
            await self._resolve_entity_aliases_with_llm(kb_id)
        await self._build_communities(kb_id, failed_chunks)

    async def finalize_partial(self, kb_id: str) -> bool:
        """Build communities from an already sufficient persisted checkpoint."""
        stats = self.db.graph_checkpoint_stats(kb_id)
        unresolved = max(0, stats["total"] - stats["succeeded"])
        success_ratio = (
            stats["succeeded"] / stats["total"] * 100
            if stats["total"]
            else 0.0
        )
        if (
            not stats["total"]
            or not stats["succeeded"]
            or success_ratio < self.settings.graph_success_threshold
        ):
            return False
        self.db.update_graph_progress(
            kb_id,
            stage=(
                "matching"
                if self.settings.graph_llm_entity_matching
                else "communities"
            ),
            current=stats["succeeded"],
            total=stats["total"],
            failed_chunks=unresolved,
        )
        try:
            await asyncio.wait_for(
                self._finalize_graph(kb_id, unresolved),
                timeout=self.settings.graph_build_timeout,
            )
            self.db.complete_graph_build(kb_id, unresolved)
            if not unresolved:
                self.db.clear_graph_checkpoints(kb_id)
            return True
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self.db.pause_graph_build(
                kb_id,
                "图社区生成达到总任务时限，已保留实体关系和文本块检查点，可继续构建",
            )
        except Exception as exc:
            self.db.pause_graph_build(
                kb_id,
                f"图社区生成暂未完成，实体关系和检查点已保留：{str(exc)[:500]}",
            )
        return False

    @staticmethod
    def _is_retryable_extraction_error(error: BaseException) -> bool:
        if isinstance(error, AIServiceError):
            return error.retryable
        return isinstance(error, (TimeoutError, ValueError, ConnectionError))

    async def _rebuild_pipeline(self, kb_id: str, chunks: list[dict[str, Any]]) -> int | None:
        stats = self.db.graph_checkpoint_stats(kb_id)
        last_error: BaseException | None = None
        fatal_error: BaseException | None = None
        configured_concurrency = max(1, self.settings.graph_concurrency)
        remaining = chunks
        self.db.update_graph_progress(
            kb_id,
            stage="extracting",
            current=stats["succeeded"],
            total=stats["total"],
            failed_chunks=max(0, stats["total"] - stats["succeeded"]),
        )
        for round_index in range(self.settings.graph_retry_rounds + 1):
            if not remaining:
                break
            divisor = 2**round_index
            concurrency = max(1, (configured_concurrency + divisor - 1) // divisor)
            target_concurrency = concurrency
            success_streak = 0
            retry_queue: list[dict[str, Any]] = []
            stage = "extracting" if round_index == 0 else "retrying"
            next_index = 0
            in_flight: dict[asyncio.Task[dict[str, Any]], dict[str, Any]] = {}

            def fill_worker_slots() -> None:
                nonlocal next_index
                while (
                    next_index < len(remaining)
                    and len(in_flight) < target_concurrency
                ):
                    chunk = remaining[next_index]
                    next_index += 1
                    task = asyncio.create_task(
                        asyncio.wait_for(
                            self._extract_chunk(chunk),
                            timeout=self.settings.graph_chunk_timeout,
                        )
                    )
                    in_flight[task] = chunk

            fill_worker_slots()
            try:
                while in_flight:
                    done, _ = await asyncio.wait(
                        in_flight,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    extractions: list[dict[str, Any]] = []
                    provider_overloaded = False
                    wave_successes = 0
                    for task in done:
                        chunk = in_flight.pop(task)
                        try:
                            result = task.result()
                        except asyncio.CancelledError:
                            raise
                        except BaseException as error:
                            last_error = error
                            retryable = self._is_retryable_extraction_error(error)
                            provider_overloaded = provider_overloaded or (
                                isinstance(error, AIServiceError)
                                and error.status_code in {408, 425, 429, 503, 504}
                            )
                            should_retry = (
                                retryable
                                and round_index < self.settings.graph_retry_rounds
                            )
                            if should_retry:
                                # A slow/transiently failing chunk goes to the
                                # tail. Healthy chunks immediately refill the
                                # released worker slot.
                                retry_queue.append(chunk)
                            elif not retryable:
                                fatal_error = error
                            extractions.append(
                                {
                                    "chunk_id": chunk["id"],
                                    "entities": [],
                                    "relationships": [],
                                    "_checkpoint_status": (
                                        "pending" if should_retry else "failed"
                                    ),
                                    "_checkpoint_error": str(error),
                                }
                            )
                        else:
                            wave_successes += 1
                            result["_checkpoint_status"] = "succeeded"
                            result["_checkpoint_error"] = None
                            extractions.append(result)

                    if extractions:
                        # Persist each completion wave instead of waiting for
                        # the slowest request in a fixed-size batch.
                        self._store_extractions(kb_id, extractions)
                    stats = self.db.graph_checkpoint_stats(kb_id)
                    self.db.update_graph_progress(
                        kb_id,
                        stage=stage,
                        current=stats["succeeded"],
                        total=stats["total"],
                        failed_chunks=max(0, stats["total"] - stats["succeeded"]),
                    )
                    if fatal_error is not None:
                        break
                    if provider_overloaded and target_concurrency > 1:
                        # Only explicit provider-pressure responses reduce the
                        # rolling window. A slow request or malformed JSON
                        # moves to the tail without penalizing healthy work.
                        target_concurrency = max(
                            1, (target_concurrency + 1) // 2
                        )
                        success_streak = 0
                    elif wave_successes:
                        success_streak += wave_successes
                        recovery_threshold = max(4, target_concurrency * 2)
                        if (
                            target_concurrency < concurrency
                            and success_streak >= recovery_threshold
                        ):
                            # Additive recovery prevents a transient 429 from
                            # leaving the rest of the pass at single concurrency.
                            target_concurrency += 1
                            success_streak = 0
                    fill_worker_slots()
            finally:
                for task in in_flight:
                    if not task.done():
                        task.cancel()
                if in_flight:
                    await asyncio.gather(*in_flight, return_exceptions=True)
            if fatal_error is not None:
                break
            remaining = retry_queue
            if remaining and round_index < self.settings.graph_retry_rounds:
                self.db.update_graph_progress(
                    kb_id,
                    stage="retrying",
                    current=stats["succeeded"],
                    total=stats["total"],
                    failed_chunks=max(0, stats["total"] - stats["succeeded"]),
                )
                delay = min(
                    60.0,
                    self.settings.graph_retry_backoff * (2**round_index)
                    + random.uniform(0, self.settings.graph_retry_backoff),
                )
                await asyncio.sleep(delay)

        stats = self.db.graph_checkpoint_stats(kb_id)
        unresolved = max(0, stats["total"] - stats["succeeded"])
        if unresolved:
            success_ratio = (
                stats["succeeded"] / stats["total"] * 100
                if stats["total"]
                else 0.0
            )
            if (
                stats["succeeded"] > 0
                and success_ratio >= self.settings.graph_success_threshold
            ):
                await self._finalize_graph(kb_id, unresolved)
                return unresolved
            self.db.update_graph_progress(
                kb_id,
                stage="retrying" if fatal_error is None else "extracting",
                current=stats["succeeded"],
                total=stats["total"],
                failed_chunks=unresolved,
            )
            detail = str(last_error or "模型未返回有效 JSON")[:260]
            reason = (
                f"模型请求出现不可重试错误，尚有 {unresolved} 个文本块未完成：{detail}；"
                "请检查模型配置后继续构建"
                if fatal_error is not None
                else f"{unresolved} 个文本块在自动降并发重试后仍未成功：{detail}；"
                "任务已暂停，继续构建只会重试未完成块"
            )
            self.db.pause_graph_build(kb_id, reason)
            return None
        await self._finalize_graph(kb_id, 0)
        return 0

    async def rebuild(self, kb_id: str, *, resume: bool = False) -> None:
        if resume:
            stats = self.db.graph_checkpoint_stats(kb_id)
            if not stats["total"]:
                self.db.set_graph_status(
                    kb_id,
                    "error",
                    "没有可继续的构建检查点，请从零重新构建",
                )
                return
            self.db.resume_graph_build(kb_id)
            chunks = self.db.pending_graph_chunks(kb_id)
        else:
            chunks = self.db.chunks_for_graph(kb_id, self.settings.graph_max_chunks)
            if not chunks:
                self.db.set_graph_status(kb_id, "error", "知识库中没有可用于建图的文本块")
                return
            self.db.clear_graph(kb_id)
            self.db.prepare_graph_checkpoints(kb_id, [chunk["id"] for chunk in chunks])
            self.db.start_graph_build(kb_id, len(chunks))
        try:
            failed_chunks = await asyncio.wait_for(
                self._rebuild_pipeline(kb_id, chunks),
                timeout=self.settings.graph_build_timeout,
            )
            if failed_chunks is None:
                return
            self.db.complete_graph_build(kb_id, failed_chunks)
            if not failed_chunks:
                self.db.clear_graph_checkpoints(kb_id)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            timeout_minutes = max(1, round(self.settings.graph_build_timeout / 60))
            self.db.pause_graph_build(
                kb_id,
                f"本轮构建达到 {timeout_minutes} 分钟上限，已自动暂停；已完成结果和检查点均已保留，可调整配置后继续构建",
            )
        except AIServiceError as exc:
            if exc.retryable:
                self.db.pause_graph_build(
                    kb_id,
                    f"模型服务暂时不可用，构建已暂停且检查点已保留：{str(exc)[:500]}",
                )
            else:
                self.db.set_graph_status(kb_id, "error", str(exc)[:1000])
        except Exception as exc:
            self.db.set_graph_status(kb_id, "error", str(exc)[:1000])
