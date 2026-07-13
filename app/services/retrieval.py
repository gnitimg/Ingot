from __future__ import annotations

import json
from typing import Any, Literal

import numpy as np

from app.config import Settings
from app.database import Database, blob_to_vector
from app.services.ai_client import AIClient, AIServiceError


RetrievalMode = Literal["vector", "graph_local", "graph_global", "hybrid"]


class RetrievalService:
    def __init__(self, db: Database, ai: AIClient, settings: Settings):
        self.db = db
        self.ai = ai
        self.settings = settings

    @staticmethod
    def _public_chunk(row: dict[str, Any], score: float) -> dict[str, Any]:
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        return {
            "id": row["id"],
            "document_id": row["document_id"],
            "filename": row["filename"],
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "page_number": row.get("page_number"),
            "metadata": metadata,
            "score": round(float(score), 5),
        }

    def _rank_vectors(
        self, rows: list[dict[str, Any]], query_vector: list[float], top_k: int
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        compatible = [row for row in rows if int(row["embedding_dim"]) == query.size]
        if not compatible:
            return []
        matrix = np.vstack([blob_to_vector(row["embedding"]) for row in compatible])
        scores = matrix @ query
        indices = np.argsort(scores)[::-1][:top_k]
        return [self._public_chunk(compatible[int(index)], float(scores[int(index)])) for index in indices]

    def _local_graph_context(
        self, kb_id: str, query: str, seed_chunk_ids: list[str], limit: int = 16
    ) -> tuple[list[dict[str, Any]], list[str]]:
        seed_entities: dict[str, dict[str, Any]] = {}
        if seed_chunk_ids:
            placeholders = ",".join("?" for _ in seed_chunk_ids)
            rows = self.db.fetch_all(
                f"""SELECT DISTINCT e.id, e.name, e.normalized_name, e.entity_type
                    FROM entity_chunks ec JOIN entities e ON e.id = ec.entity_id
                    WHERE e.kb_id = ? AND ec.chunk_id IN ({placeholders})""",
                [kb_id, *seed_chunk_ids],
            )
            seed_entities.update({row["id"]: row for row in rows})

        normalized_query = query.casefold()
        name_matches = self.db.fetch_all(
            "SELECT id, name, normalized_name, entity_type FROM entities WHERE kb_id = ?", (kb_id,)
        )
        for entity in name_matches:
            name = str(entity["normalized_name"])
            if len(name) >= 2 and name in normalized_query:
                seed_entities[entity["id"]] = entity

        entity_ids = list(seed_entities)
        if not entity_ids:
            return [], []
        placeholders = ",".join("?" for _ in entity_ids)
        relationships = self.db.fetch_all(
            f"""SELECT r.id, r.source_id, r.target_id, r.predicate, r.description, r.weight,
                       r.evidence_chunk_id, source.name AS source, target.name AS target
                FROM relationships r
                JOIN entities source ON source.id = r.source_id
                JOIN entities target ON target.id = r.target_id
                WHERE r.kb_id = ? AND (r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders}))
                ORDER BY r.weight DESC LIMIT ?""",
            [kb_id, *entity_ids, *entity_ids, limit],
        )
        facts = [
            {
                "id": relation["id"],
                "source": relation["source"],
                "target": relation["target"],
                "predicate": relation["predicate"],
                "description": relation["description"],
                "weight": relation["weight"],
                "evidence_chunk_id": relation["evidence_chunk_id"],
            }
            for relation in relationships
        ]
        expanded_entities = set(entity_ids)
        for relation in relationships:
            expanded_entities.add(relation["source_id"])
            expanded_entities.add(relation["target_id"])
        expanded_placeholders = ",".join("?" for _ in expanded_entities)
        related_rows = self.db.fetch_all(
            f"""SELECT ec.chunk_id, SUM(ec.mention_count) AS relevance
                FROM entity_chunks ec
                WHERE ec.entity_id IN ({expanded_placeholders})
                GROUP BY ec.chunk_id ORDER BY relevance DESC LIMIT ?""",
            [*expanded_entities, self.settings.default_top_k],
        )
        related_chunk_ids = [row["chunk_id"] for row in related_rows]
        return facts, related_chunk_ids

    def _global_graph_context(
        self, kb_id: str, query_vector: list[float], top_k: int = 4
    ) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT id, title, summary, member_count, embedding, embedding_dim
               FROM communities WHERE kb_id = ?""",
            (kb_id,),
        )
        if not rows:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        compatible = [row for row in rows if row["embedding"] and int(row["embedding_dim"]) == query.size]
        if not compatible:
            return []
        scores = np.vstack([blob_to_vector(row["embedding"]) for row in compatible]) @ query
        indices = np.argsort(scores)[::-1][:top_k]
        return [
            {
                "id": compatible[int(index)]["id"],
                "title": compatible[int(index)]["title"],
                "summary": compatible[int(index)]["summary"],
                "member_count": compatible[int(index)]["member_count"],
                "score": round(float(scores[int(index)]), 5),
            }
            for index in indices
        ]

    async def search(
        self, kb_id: str, query: str, mode: RetrievalMode = "hybrid", top_k: int | None = None
    ) -> dict[str, Any]:
        limit = top_k or self.settings.default_top_k
        query_vector = (await self.ai.embed([query]))[0]
        chunks: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        communities: list[dict[str, Any]] = []
        warnings: list[str] = []
        rerank_used = False

        if mode in {"vector", "hybrid", "graph_local"}:
            vector_limit = limit if mode != "graph_local" else min(4, limit)
            candidate_limit = (
                max(vector_limit, self.settings.rerank_candidates)
                if self.settings.rerank_configured
                else vector_limit
            )
            chunks = self._rank_vectors(
                self.db.chunks_for_search(kb_id), query_vector, candidate_limit
            )
            if self.settings.rerank_configured and chunks:
                try:
                    ranked = await self.ai.rerank(
                        query,
                        [chunk["content"] for chunk in chunks],
                        vector_limit,
                    )
                    if ranked:
                        chunks = [
                            {**chunks[int(item["index"])], "score": round(float(item["score"]), 5), "score_type": "rerank"}
                            for item in ranked
                        ]
                        rerank_used = True
                    else:
                        chunks = chunks[:vector_limit]
                except AIServiceError as exc:
                    warnings.append(f"Reranker 调用失败，已回退到向量排序：{exc}")
                    chunks = chunks[:vector_limit]
            else:
                chunks = chunks[:vector_limit]

        if mode in {"graph_local", "hybrid"}:
            facts, related_ids = self._local_graph_context(
                kb_id, query, [chunk["id"] for chunk in chunks]
            )
            existing_ids = {chunk["id"] for chunk in chunks}
            extra_ids = [chunk_id for chunk_id in related_ids if chunk_id not in existing_ids]
            for row in self.db.get_chunks(extra_ids[: max(0, limit - len(chunks))]):
                chunks.append(self._public_chunk(row, 0.0))

        if mode == "graph_global":
            communities = self._global_graph_context(kb_id, query_vector)

        return {
            "mode": mode,
            "query": query,
            "chunks": chunks,
            "facts": facts,
            "communities": communities,
            "rerank_used": rerank_used,
            "warnings": warnings,
        }

    @staticmethod
    def build_messages(
        query: str, retrieval: dict[str, Any], history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        context_parts: list[str] = []
        for index, chunk in enumerate(retrieval["chunks"], start=1):
            location = f"，第{chunk['page_number']}页" if chunk.get("page_number") else ""
            context_parts.append(
                f"[来源{index}] 文件：{chunk['filename']}{location}\n{chunk['content']}"
            )
        for index, fact in enumerate(retrieval["facts"], start=1):
            context_parts.append(
                f"[图关系{index}] {fact['source']} —{fact['predicate']}→ {fact['target']}。{fact['description']}"
            )
        for index, community in enumerate(retrieval["communities"], start=1):
            context_parts.append(
                f"[图社区{index}] {community['title']}（{community['member_count']} 个实体）\n{community['summary']}"
            )
        context = "\n\n".join(context_parts) or "（未检索到相关证据）"
        system = """你是知识库问答助手。请只根据提供的证据回答，并遵守：
1. 先直接回答问题，再补充必要解释；
2. 对事实使用 [来源N]、[图关系N] 或 [图社区N] 标注依据；
3. 若证据不足，明确说明不足，不要凭常识编造；
4. 保留原文中的数字、单位和限定条件；
5. 使用与用户问题相同的语言回答。

可用证据：
""" + context
        cleaned_history = [
            {"role": item["role"], "content": item["content"][:6000]}
            for item in history[-10:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        return [{"role": "system", "content": system}, *cleaned_history, {"role": "user", "content": query}]
