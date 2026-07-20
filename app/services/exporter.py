from __future__ import annotations

import csv
import html
import io
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.database import Database, blob_to_vector


EXPORT_DEFINITIONS: dict[str, dict[str, Any]] = {
    "snapshot": {
        "label": "全量快照",
        "description": "文档、文本块、实体、关系、社区与检查点；JSON 另含完整向量",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "summary": {
        "label": "知识库总览",
        "description": "知识库信息、规模统计和构建状态",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "originals": {
        "label": "原始文档",
        "description": "导入时保存的原始文件，保持原扩展名",
        "formats": [{"value": "zip", "label": "ZIP"}],
    },
    "documents": {
        "label": "文档目录",
        "description": "文件名、哈希、解析方式、页数和状态",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "csv", "label": "CSV"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "chunks": {
        "label": "文本块",
        "description": "切分后的正文、页码、序号和元数据",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "jsonl", "label": "JSON Lines"},
            {"value": "json", "label": "JSON"},
            {"value": "csv", "label": "CSV"},
        ],
    },
    "vectors": {
        "label": "向量索引",
        "description": "文本块 ID、来源和完整浮点向量",
        "formats": [
            {"value": "npz", "label": "NumPy NPZ"},
            {"value": "jsonl", "label": "JSON Lines"},
            {"value": "csv", "label": "CSV"},
        ],
    },
    "entities": {
        "label": "实体",
        "description": "实体名称、类型、说明和证据提及次数",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "csv", "label": "CSV"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "relationships": {
        "label": "关系",
        "description": "源实体、目标实体、关系、权重和证据来源",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "csv", "label": "CSV"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "graph": {
        "label": "完整图",
        "description": "图交换格式或可视化画布；PNG/SVG 展示前 500 个重点实体",
        "formats": [
            {"value": "md", "label": "Markdown + Mermaid"},
            {"value": "graphml", "label": "GraphML"},
            {"value": "gexf", "label": "GEXF"},
            {"value": "json", "label": "JSON"},
            {"value": "svg", "label": "SVG 画布"},
            {"value": "png", "label": "PNG 画布"},
        ],
    },
    "communities": {
        "label": "图社区",
        "description": "社区标题、摘要、成员数量和实体清单",
        "formats": [
            {"value": "md", "label": "Markdown"},
            {"value": "csv", "label": "CSV"},
            {"value": "json", "label": "JSON"},
        ],
    },
    "checkpoints": {
        "label": "构建检查点",
        "description": "每个文本块的构建状态、尝试次数和最近错误",
        "formats": [
            {"value": "csv", "label": "CSV"},
            {"value": "json", "label": "JSON"},
        ],
    },
}


MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "jsonl": "application/x-ndjson; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "npz": "application/octet-stream",
    "graphml": "application/graphml+xml",
    "gexf": "application/gexf+xml",
    "svg": "image/svg+xml",
    "png": "image/png",
    "zip": "application/zip",
}


@dataclass(frozen=True)
class ExportArtifact:
    filename: str
    media_type: str
    content: bytes


def _safe_name(value: str, fallback: str = "knowledge-base") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    return cleaned[:100] or fallback


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return (
        "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in rows)
        + ("\n" if rows else "")
    ).encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]], fieldnames: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


class KnowledgeBaseExporter:
    def __init__(self, db: Database):
        self.db = db

    def options(self, kb_id: str) -> list[dict[str, Any]]:
        counts = {
            "snapshot": 1,
            "summary": 1,
            "originals": self._count("documents", kb_id),
            "documents": self._count("documents", kb_id),
            "chunks": self._count("chunks", kb_id),
            "vectors": self._count("chunks", kb_id),
            "entities": self._count("entities", kb_id),
            "relationships": self._count("relationships", kb_id),
            "graph": self._count("entities", kb_id),
            "communities": self._count("communities", kb_id),
            "checkpoints": self._count("graph_build_chunks", kb_id),
        }
        return [
            {
                "kind": kind,
                **definition,
                "count": counts[kind],
                "available": kind in {"snapshot", "summary"} or counts[kind] > 0,
            }
            for kind, definition in EXPORT_DEFINITIONS.items()
        ]

    def _count(self, table: str, kb_id: str) -> int:
        row = self.db.fetch_one(
            f"SELECT COUNT(*) AS count FROM {table} WHERE kb_id = ?",
            (kb_id,),
        )
        return int(row["count"] if row else 0)

    def build(
        self,
        kb_id: str,
        selections: list[dict[str, str]],
        bundle: bool = False,
    ) -> ExportArtifact:
        knowledge_base = self.db.get_knowledge_base(kb_id)
        if not knowledge_base:
            raise ValueError("知识库不存在")
        artifacts = [
            self._build_one(
                knowledge_base,
                selection["kind"],
                selection["format"],
            )
            for selection in selections
        ]
        if len(artifacts) == 1 and not bundle:
            return artifacts[0]
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for artifact in artifacts:
                archive.writestr(artifact.filename, artifact.content)
        base = _safe_name(str(knowledge_base["name"]))
        return ExportArtifact(
            filename=f"{base}-exports.zip",
            media_type=MEDIA_TYPES["zip"],
            content=stream.getvalue(),
        )

    def _build_one(
        self,
        knowledge_base: dict[str, Any],
        kind: str,
        format_name: str,
    ) -> ExportArtifact:
        definition = EXPORT_DEFINITIONS.get(kind)
        allowed = {
            item["value"] for item in definition.get("formats", [])
        } if definition else set()
        if format_name not in allowed:
            raise ValueError(f"{kind} 不支持 {format_name} 格式")
        kb_id = str(knowledge_base["id"])
        base = _safe_name(str(knowledge_base["name"]))
        if kind == "snapshot":
            content = self._snapshot(knowledge_base, format_name)
        elif kind == "summary":
            content = self._summary(knowledge_base, format_name)
        elif kind == "originals":
            content = self._originals(kb_id)
        elif kind == "documents":
            content = self._tabular(
                self._documents(kb_id),
                format_name,
                "文档目录",
            )
        elif kind == "chunks":
            content = self._chunks(kb_id, format_name)
        elif kind == "vectors":
            content = self._vectors(kb_id, format_name)
        elif kind == "entities":
            content = self._tabular(
                self._entities(kb_id),
                format_name,
                "实体",
            )
        elif kind == "relationships":
            content = self._tabular(
                self._relationships(kb_id),
                format_name,
                "关系",
            )
        elif kind == "graph":
            content = self._graph(kb_id, str(knowledge_base["name"]), format_name)
        elif kind == "communities":
            content = self._tabular(
                self._communities(kb_id),
                format_name,
                "图社区",
            )
        elif kind == "checkpoints":
            content = self._tabular(
                self._checkpoints(kb_id),
                format_name,
                "构建检查点",
            )
        else:
            raise ValueError("未知导出类型")
        return ExportArtifact(
            filename=f"{base}-{kind}.{format_name}",
            media_type=MEDIA_TYPES[format_name],
            content=content,
        )

    def _snapshot(self, kb: dict[str, Any], format_name: str) -> bytes:
        kb_id = str(kb["id"])
        if format_name == "json":
            return _json_bytes(
                {
                    "knowledge_base": {
                        key: value
                        for key, value in kb.items()
                        if key not in {"password_hash", "has_password"}
                    },
                    "documents": self._documents(kb_id),
                    "chunks": self._chunks_rows(kb_id, include_vectors=True),
                    "entities": self._entities(kb_id),
                    "relationships": self._relationships(kb_id),
                    "communities": self._communities(kb_id),
                    "checkpoints": self._checkpoints(kb_id),
                }
            )
        sections = [
            self._summary(kb, "md").decode("utf-8").rstrip(),
            self._tabular(self._documents(kb_id), "md", "文档目录").decode("utf-8").rstrip(),
            self._chunks(kb_id, "md").decode("utf-8").rstrip(),
            self._tabular(self._entities(kb_id), "md", "实体").decode("utf-8").rstrip(),
            self._tabular(self._relationships(kb_id), "md", "关系").decode("utf-8").rstrip(),
            self._tabular(self._communities(kb_id), "md", "图社区").decode("utf-8").rstrip(),
            self._tabular(self._checkpoints(kb_id), "md", "构建检查点").decode("utf-8").rstrip(),
        ]
        return ("\n\n---\n\n".join(sections) + "\n").encode("utf-8")

    def _summary(self, kb: dict[str, Any], format_name: str) -> bytes:
        value = {
            key: kb.get(key)
            for key in (
                "id",
                "name",
                "description",
                "document_count",
                "chunk_count",
                "entity_count",
                "relationship_count",
                "community_count",
                "graph_status",
                "graph_stage",
                "graph_progress_current",
                "graph_progress_total",
                "graph_failed_chunks",
                "graph_error",
                "created_at",
                "updated_at",
            )
        }
        if format_name == "json":
            return _json_bytes(value)
        lines = [
            f"# {value['name']}",
            "",
            value["description"] or "（无描述）",
            "",
            "## 规模",
            "",
            f"- 文档：{value['document_count']}",
            f"- 文本块：{value['chunk_count']}",
            f"- 实体：{value['entity_count']}",
            f"- 关系：{value['relationship_count']}",
            f"- 图社区：{value['community_count']}",
            "",
            "## 图谱状态",
            "",
            f"- 状态：{value['graph_status']}",
            f"- 阶段：{value['graph_stage'] or '—'}",
            f"- 进度：{value['graph_progress_current']}/{value['graph_progress_total']}",
            f"- 未完成块：{value['graph_failed_chunks']}",
        ]
        if value["graph_error"]:
            lines.extend(["", f"> {value['graph_error']}"])
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _documents(self, kb_id: str, *, include_path: bool = False) -> list[dict[str, Any]]:
        columns = (
            "id, filename, stored_path, file_type, size_bytes, sha256, chunk_count, "
            "status, extraction_method, page_count, ocr_page_count, warning, error, created_at"
        )
        rows = self.db.fetch_all(
            f"SELECT {columns} FROM documents WHERE kb_id = ? ORDER BY created_at, filename",
            (kb_id,),
        )
        if not include_path:
            for row in rows:
                row.pop("stored_path", None)
        return rows

    def _originals(self, kb_id: str) -> bytes:
        documents = self._documents(kb_id, include_path=True)
        stream = io.BytesIO()
        used_names: set[str] = set()
        missing: list[str] = []
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for document in documents:
                path = Path(str(document.get("stored_path") or ""))
                if not path.is_file():
                    missing.append(str(document["filename"]))
                    continue
                filename = Path(str(document["filename"])).name or path.name
                candidate = filename
                counter = 2
                while candidate.casefold() in used_names:
                    candidate = f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
                    counter += 1
                used_names.add(candidate.casefold())
                archive.write(path, f"documents/{candidate}")
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "documents": [
                            {key: value for key, value in document.items() if key != "stored_path"}
                            for document in documents
                        ],
                        "missing_files": missing,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return stream.getvalue()

    def _chunks_rows(self, kb_id: str, *, include_vectors: bool = False) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT c.id, c.document_id, d.filename, c.chunk_index,
                      c.page_number, c.token_count, c.metadata_json,
                      c.content, c.embedding, c.embedding_dim, c.created_at
               FROM chunks c
               JOIN documents d ON d.id = c.document_id
               WHERE c.kb_id = ?
               ORDER BY d.created_at, c.chunk_index""",
            (kb_id,),
        )
        for row in rows:
            try:
                row["metadata"] = json.loads(str(row.pop("metadata_json") or "{}"))
            except ValueError:
                row["metadata"] = {}
            blob = row.pop("embedding")
            if include_vectors:
                row["embedding"] = blob_to_vector(blob).astype(float).tolist()
            row["embedding_dim"] = int(row.get("embedding_dim") or 0)
        return rows

    def _chunks(self, kb_id: str, format_name: str) -> bytes:
        rows = self._chunks_rows(kb_id)
        if format_name == "md":
            parts = ["# 文本块", ""]
            for row in rows:
                location = f" · 第 {row['page_number']} 页" if row.get("page_number") else ""
                parts.extend(
                    [
                        f"## {row['filename']} · Chunk {int(row['chunk_index']) + 1}{location}",
                        "",
                        str(row["content"]),
                        "",
                        f"`id: {row['id']}` · `embedding_dim: {row['embedding_dim']}`",
                        "",
                    ]
                )
            return "\n".join(parts).encode("utf-8")
        if format_name == "json":
            return _json_bytes(rows)
        if format_name == "jsonl":
            return _jsonl_bytes(rows)
        csv_rows = [
            {
                **row,
                "metadata": json.dumps(row.get("metadata") or {}, ensure_ascii=False),
            }
            for row in rows
        ]
        return _csv_bytes(csv_rows, list(csv_rows[0].keys()) if csv_rows else [
            "id", "document_id", "filename", "chunk_index", "page_number",
            "token_count", "content", "embedding_dim", "created_at", "metadata",
        ])

    def _vectors(self, kb_id: str, format_name: str) -> bytes:
        rows = self._chunks_rows(kb_id, include_vectors=True)
        if format_name == "npz":
            stream = io.BytesIO()
            dimensions = {len(row["embedding"]) for row in rows}
            matrix = (
                np.asarray([row["embedding"] for row in rows], dtype=np.float32)
                if len(dimensions) <= 1
                else np.asarray([np.asarray(row["embedding"], dtype=np.float32) for row in rows], dtype=object)
            )
            np.savez_compressed(
                stream,
                vectors=matrix,
                ids=np.asarray([row["id"] for row in rows]),
                document_ids=np.asarray([row["document_id"] for row in rows]),
                filenames=np.asarray([row["filename"] for row in rows]),
                chunk_indices=np.asarray([row["chunk_index"] for row in rows], dtype=np.int32),
            )
            return stream.getvalue()
        vector_rows = [
            {
                "id": row["id"],
                "document_id": row["document_id"],
                "filename": row["filename"],
                "chunk_index": row["chunk_index"],
                "page_number": row["page_number"],
                "embedding_dim": row["embedding_dim"],
                "embedding": row["embedding"],
            }
            for row in rows
        ]
        if format_name == "jsonl":
            return _jsonl_bytes(vector_rows)
        csv_rows = [
            {
                **row,
                "embedding": " ".join(f"{float(value):.8g}" for value in row["embedding"]),
            }
            for row in vector_rows
        ]
        return _csv_bytes(
            csv_rows,
            ["id", "document_id", "filename", "chunk_index", "page_number", "embedding_dim", "embedding"],
        )

    def _entities(self, kb_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT e.id, e.name, e.normalized_name, e.entity_type,
                      e.description, COUNT(ec.chunk_id) AS source_chunks,
                      COALESCE(SUM(ec.mention_count), 0) AS mention_count,
                      e.created_at
               FROM entities e
               LEFT JOIN entity_chunks ec ON ec.entity_id = e.id
               WHERE e.kb_id = ?
               GROUP BY e.id
               ORDER BY mention_count DESC, e.name""",
            (kb_id,),
        )

    def _relationships(self, kb_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT r.id, r.source_id, source.name AS source,
                      r.target_id, target.name AS target,
                      r.predicate, r.description, r.weight,
                      r.evidence_chunk_id, d.filename AS evidence_file,
                      c.chunk_index AS evidence_chunk_index, r.created_at
               FROM relationships r
               JOIN entities source ON source.id = r.source_id
               JOIN entities target ON target.id = r.target_id
               LEFT JOIN chunks c ON c.id = r.evidence_chunk_id
               LEFT JOIN documents d ON d.id = c.document_id
               WHERE r.kb_id = ?
               ORDER BY r.weight DESC, source.name, target.name""",
            (kb_id,),
        )

    def _communities(self, kb_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """SELECT id, title, summary, member_count, embedding_dim, created_at
               FROM communities WHERE kb_id = ?
               ORDER BY member_count DESC, title""",
            (kb_id,),
        )
        memberships = self.db.fetch_all(
            """SELECT ce.community_id, e.id, e.name, e.entity_type
               FROM community_entities ce
               JOIN entities e ON e.id = ce.entity_id
               WHERE e.kb_id = ?
               ORDER BY ce.community_id, e.name""",
            (kb_id,),
        )
        by_community: dict[str, list[dict[str, str]]] = {}
        for membership in memberships:
            by_community.setdefault(str(membership["community_id"]), []).append(
                {
                    "id": str(membership["id"]),
                    "name": str(membership["name"]),
                    "type": str(membership["entity_type"]),
                }
            )
        for row in rows:
            row["members"] = by_community.get(str(row["id"]), [])
        return rows

    def _checkpoints(self, kb_id: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT checkpoint.position, checkpoint.chunk_id,
                      d.filename, c.chunk_index, checkpoint.status,
                      checkpoint.attempts, checkpoint.error,
                      checkpoint.updated_at
               FROM graph_build_chunks checkpoint
               JOIN chunks c ON c.id = checkpoint.chunk_id
               JOIN documents d ON d.id = c.document_id
               WHERE checkpoint.kb_id = ?
               ORDER BY checkpoint.position""",
            (kb_id,),
        )

    def _tabular(
        self,
        rows: list[dict[str, Any]],
        format_name: str,
        title: str,
    ) -> bytes:
        if format_name == "json":
            return _json_bytes(rows)
        normalized = [
            {
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in row.items()
            }
            for row in rows
        ]
        fields = list(normalized[0].keys()) if normalized else ["id"]
        if format_name == "csv":
            return _csv_bytes(normalized, fields)
        lines = [f"# {title}", "", f"共 {len(rows)} 条记录。", ""]
        if not normalized:
            lines.append("（无数据）")
            return ("\n".join(lines) + "\n").encode("utf-8")
        lines.extend(
            [
                "| " + " | ".join(fields) + " |",
                "|" + "|".join("---" for _ in fields) + "|",
            ]
        )
        for row in normalized:
            lines.append("| " + " | ".join(_markdown_cell(row.get(field)) for field in fields) + " |")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _graph_rows(self, kb_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return self._entities(kb_id), self._relationships(kb_id)

    def _graph(self, kb_id: str, name: str, format_name: str) -> bytes:
        nodes, edges = self._graph_rows(kb_id)
        if format_name == "json":
            return _json_bytes({"nodes": nodes, "edges": edges})
        if format_name == "graphml":
            return self._graphml(nodes, edges)
        if format_name == "gexf":
            return self._gexf(nodes, edges)
        if format_name == "md":
            return self._graph_markdown(name, nodes, edges)
        if format_name == "svg":
            return self._graph_svg(name, nodes, edges)
        return self._graph_png(name, nodes, edges)

    @staticmethod
    def _graphml(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> bytes:
        namespace = "http://graphml.graphdrawing.org/xmlns"
        ET.register_namespace("", namespace)
        root = ET.Element(f"{{{namespace}}}graphml")
        for key_id, target, name, value_type in (
            ("name", "node", "name", "string"),
            ("type", "node", "type", "string"),
            ("description", "all", "description", "string"),
            ("predicate", "edge", "predicate", "string"),
            ("weight", "edge", "weight", "double"),
        ):
            ET.SubElement(
                root,
                f"{{{namespace}}}key",
                id=key_id,
                **{"for": target, "attr.name": name, "attr.type": value_type},
            )
        graph = ET.SubElement(root, f"{{{namespace}}}graph", edgedefault="directed")
        for node in nodes:
            element = ET.SubElement(graph, f"{{{namespace}}}node", id=str(node["id"]))
            for key, value in (
                ("name", node["name"]),
                ("type", node["entity_type"]),
                ("description", node["description"]),
            ):
                ET.SubElement(element, f"{{{namespace}}}data", key=key).text = str(value or "")
        for edge in edges:
            element = ET.SubElement(
                graph,
                f"{{{namespace}}}edge",
                id=str(edge["id"]),
                source=str(edge["source_id"]),
                target=str(edge["target_id"]),
            )
            for key, value in (
                ("predicate", edge["predicate"]),
                ("description", edge["description"]),
                ("weight", edge["weight"]),
            ):
                ET.SubElement(element, f"{{{namespace}}}data", key=key).text = str(value or "")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _gexf(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> bytes:
        namespace = "http://www.gexf.net/1.3"
        ET.register_namespace("", namespace)
        root = ET.Element(f"{{{namespace}}}gexf", version="1.3")
        graph = ET.SubElement(
            root,
            f"{{{namespace}}}graph",
            mode="static",
            defaultedgetype="directed",
        )
        node_container = ET.SubElement(graph, f"{{{namespace}}}nodes")
        for node in nodes:
            ET.SubElement(
                node_container,
                f"{{{namespace}}}node",
                id=str(node["id"]),
                label=str(node["name"]),
            )
        edge_container = ET.SubElement(graph, f"{{{namespace}}}edges")
        for edge in edges:
            ET.SubElement(
                edge_container,
                f"{{{namespace}}}edge",
                id=str(edge["id"]),
                source=str(edge["source_id"]),
                target=str(edge["target_id"]),
                label=str(edge["predicate"]),
                weight=str(edge["weight"]),
            )
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _graph_markdown(
        name: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> bytes:
        top_nodes = nodes[:80]
        node_index = {str(node["id"]): index for index, node in enumerate(top_nodes)}
        diagram_edges = [
            edge for edge in edges
            if str(edge["source_id"]) in node_index and str(edge["target_id"]) in node_index
        ][:160]
        lines = [
            f"# {name} · 知识图谱",
            "",
            f"- 实体：{len(nodes)}",
            f"- 关系：{len(edges)}",
            "",
            "## Mermaid 预览",
            "",
            "```mermaid",
            "flowchart LR",
        ]
        for node in top_nodes:
            label = str(node["name"]).replace('"', "'").replace("\n", " ")
            lines.append(f'  n{node_index[str(node["id"])]}["{label}"]')
        for edge in diagram_edges:
            predicate = str(edge["predicate"]).replace("|", "/").replace("\n", " ")
            lines.append(
                f"  n{node_index[str(edge['source_id'])]} -->|{predicate}| "
                f"n{node_index[str(edge['target_id'])]}"
            )
        lines.extend(["```", "", "## 实体", ""])
        lines.extend(
            f"- **{node['name']}**（{node['entity_type']}）：{node['description'] or '—'}"
            for node in nodes
        )
        lines.extend(["", "## 关系", ""])
        lines.extend(
            f"- **{edge['source']}** —{edge['predicate']}→ **{edge['target']}**"
            f"（权重 {edge['weight']}）：{edge['description'] or '—'}"
            for edge in edges
        )
        return ("\n".join(lines) + "\n").encode("utf-8")

    @staticmethod
    def _visual_layout(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        width: int = 2400,
        height: int = 1600,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, tuple[float, float]]]:
        visual_nodes = nodes[:500]
        ids = {str(node["id"]) for node in visual_nodes}
        visual_edges = [
            edge for edge in edges
            if str(edge["source_id"]) in ids and str(edge["target_id"]) in ids
        ]
        positions: dict[str, tuple[float, float]] = {}
        golden_angle = math.pi * (3 - math.sqrt(5))
        max_radius = min(width, height) * 0.44
        for index, node in enumerate(visual_nodes):
            radius = 0 if len(visual_nodes) <= 1 else 50 + math.sqrt(index / max(1, len(visual_nodes) - 1)) * (max_radius - 50)
            angle = index * golden_angle
            positions[str(node["id"])] = (
                width / 2 + math.cos(angle) * radius,
                height / 2 + math.sin(angle) * radius,
            )
        return visual_nodes, visual_edges, positions

    @classmethod
    def _graph_svg(
        cls,
        name: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> bytes:
        width, height = 2400, 1600
        visual_nodes, visual_edges, positions = cls._visual_layout(nodes, edges, width, height)
        degree: dict[str, int] = {str(node["id"]): 0 for node in visual_nodes}
        for edge in visual_edges:
            degree[str(edge["source_id"])] += 1
            degree[str(edge["target_id"])] += 1
        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            "<defs><marker id=\"arrow\" viewBox=\"0 0 10 10\" refX=\"8\" refY=\"5\" markerWidth=\"5\" markerHeight=\"5\" orient=\"auto-start-reverse\"><path d=\"M 0 0 L 10 5 L 0 10 z\" fill=\"#7b8781\"/></marker></defs>",
            '<rect width="100%" height="100%" fill="#f3f1e8"/>',
            f'<text x="40" y="55" font-family="system-ui,sans-serif" font-size="30" fill="#18231f">{html.escape(name)} · {len(nodes)} 实体 / {len(edges)} 关系</text>',
            '<g stroke="#7b8781" stroke-opacity=".28" stroke-width="1.2" marker-end="url(#arrow)">',
        ]
        for edge in visual_edges:
            source = positions[str(edge["source_id"])]
            target = positions[str(edge["target_id"])]
            lines.append(f'<line x1="{source[0]:.1f}" y1="{source[1]:.1f}" x2="{target[0]:.1f}" y2="{target[1]:.1f}"/>')
        lines.append("</g><g>")
        top_labels = {str(node["id"]) for node in sorted(visual_nodes, key=lambda item: -degree[str(item["id"])])[:50]}
        palette = ["#477ae8", "#df6c42", "#87a91c", "#8c61c9", "#299d8f", "#c65c8b", "#69736d"]
        for node in visual_nodes:
            node_id = str(node["id"])
            x, y = positions[node_id]
            color = palette[sum(ord(char) for char in str(node["entity_type"])) % len(palette)]
            radius = 8 + min(12, math.sqrt(degree[node_id]) * 2)
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" stroke="#fff" stroke-width="2"/>')
            if node_id in top_labels:
                lines.append(
                    f'<text x="{x + radius + 5:.1f}" y="{y + 4:.1f}" '
                    'font-family="system-ui,sans-serif" font-size="12" fill="#25322d">'
                    f'{html.escape(str(node["name"])[:40])}</text>'
                )
        lines.extend(["</g>", "</svg>"])
        return "".join(lines).encode("utf-8")

    @classmethod
    def _graph_png(
        cls,
        name: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> bytes:
        width, height = 2400, 1600
        visual_nodes, visual_edges, positions = cls._visual_layout(nodes, edges, width, height)
        image = Image.new("RGB", (width, height), "#f3f1e8")
        draw = ImageDraw.Draw(image, "RGBA")
        degree: dict[str, int] = {str(node["id"]): 0 for node in visual_nodes}
        for edge in visual_edges:
            source_id, target_id = str(edge["source_id"]), str(edge["target_id"])
            degree[source_id] += 1
            degree[target_id] += 1
            draw.line([positions[source_id], positions[target_id]], fill=(104, 117, 110, 58), width=2)
        palette = ["#477ae8", "#df6c42", "#87a91c", "#8c61c9", "#299d8f", "#c65c8b", "#69736d"]
        for node in visual_nodes:
            node_id = str(node["id"])
            x, y = positions[node_id]
            radius = 8 + min(12, math.sqrt(degree[node_id]) * 2)
            color = palette[sum(ord(char) for char in str(node["entity_type"])) % len(palette)]
            draw.ellipse(
                [x - radius, y - radius, x + radius, y + radius],
                fill=color,
                outline="#ffffff",
                width=2,
            )
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont
        font = ImageFont.load_default()
        for path in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ):
            if path.is_file():
                try:
                    font = ImageFont.truetype(str(path), 28)
                    break
                except OSError:
                    pass
        draw.text(
            (40, 30),
            f"{name} · {len(nodes)} 实体 / {len(edges)} 关系",
            fill="#18231f",
            font=font,
        )
        stream = io.BytesIO()
        image.save(stream, "PNG", optimize=True)
        return stream.getvalue()
