from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from app.database import Database
from app.services.exporter import KnowledgeBaseExporter


def seeded_exporter(tmp_path: Path) -> tuple[KnowledgeBaseExporter, str]:
    database = Database(tmp_path / "export.db")
    database.initialize()
    source = tmp_path / "source.md"
    source.write_text("# 测试\n\n设备产生工单。", encoding="utf-8")
    knowledge_base = database.create_knowledge_base("导出测试", "完整数据")
    kb_id = str(knowledge_base["id"])
    document_id = database.create_document(
        kb_id,
        source.name,
        str(source),
        "md",
        source.stat().st_size,
        "export-hash",
    )
    database.insert_chunks(
        document_id,
        kb_id,
        [
            {
                "id": "chunk-1",
                "content": "设备产生工单。",
                "chunk_index": 0,
                "page_number": 1,
                "token_count": 6,
                "metadata": {"section": "测试"},
                "embedding": [0.1, 0.2, 0.3],
            }
        ],
    )
    database.set_document_status(
        document_id,
        "ready",
        chunk_count=1,
        extraction_method="native",
        page_count=1,
    )
    with database.connection() as connection:
        connection.executemany(
            """INSERT INTO entities
               (id, kb_id, name, normalized_name, entity_type, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("entity-device", kb_id, "设备", "设备", "设备", "生产设备", "now"),
                ("entity-order", kb_id, "工单", "工单", "业务", "维修工单", "now"),
            ],
        )
        connection.executemany(
            """INSERT INTO entity_chunks(entity_id, chunk_id, mention_count)
               VALUES (?, 'chunk-1', ?)""",
            [("entity-device", 1), ("entity-order", 1)],
        )
        connection.execute(
            """INSERT INTO relationships
               (id, kb_id, source_id, target_id, predicate, description,
                weight, evidence_chunk_id, created_at)
               VALUES ('relation-1', ?, 'entity-device', 'entity-order',
                       '产生', '设备产生维修工单', 1.5, 'chunk-1', 'now')""",
            (kb_id,),
        )
        connection.execute(
            """INSERT INTO communities
               (id, kb_id, title, summary, member_count, created_at)
               VALUES ('community-1', ?, '设备维护', '设备与工单主题', 2, 'now')""",
            (kb_id,),
        )
        connection.executemany(
            """INSERT INTO community_entities(community_id, entity_id)
               VALUES ('community-1', ?)""",
            [("entity-device",), ("entity-order",)],
        )
    database.prepare_graph_checkpoints(kb_id, ["chunk-1"])
    return KnowledgeBaseExporter(database), kb_id


def test_export_options_and_portable_formats(tmp_path: Path):
    exporter, kb_id = seeded_exporter(tmp_path)

    options = {option["kind"]: option for option in exporter.options(kb_id)}
    assert options["documents"]["count"] == 1
    assert options["vectors"]["available"] is True
    assert options["graph"]["count"] == 2
    assert {item["value"] for item in options["graph"]["formats"]} == {
        "md", "graphml", "gexf", "json", "svg", "png"
    }

    graphml = exporter.build(kb_id, [{"kind": "graph", "format": "graphml"}])
    assert graphml.media_type == "application/graphml+xml"
    assert ET.fromstring(graphml.content).tag.endswith("graphml")

    svg = exporter.build(kb_id, [{"kind": "graph", "format": "svg"}])
    assert svg.content.startswith(b"<svg")

    png = exporter.build(kb_id, [{"kind": "graph", "format": "png"}])
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")

    vectors = exporter.build(kb_id, [{"kind": "vectors", "format": "npz"}])
    with np.load(io.BytesIO(vectors.content)) as archive:
        assert archive["vectors"].shape == (1, 3)
        assert np.allclose(archive["vectors"][0], [0.1, 0.2, 0.3])


def test_batch_export_is_always_a_zip(tmp_path: Path):
    exporter, kb_id = seeded_exporter(tmp_path)

    artifact = exporter.build(
        kb_id,
        [{"kind": "summary", "format": "md"}],
        bundle=True,
    )
    assert artifact.filename.endswith("-exports.zip")
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        names = archive.namelist()
        assert names == ["导出测试-summary.md"]
        assert "# 导出测试" in archive.read(names[0]).decode("utf-8")

    artifact = exporter.build(
        kb_id,
        [
            {"kind": "documents", "format": "json"},
            {"kind": "communities", "format": "json"},
        ],
        bundle=True,
    )
    with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
        document_name = next(name for name in archive.namelist() if name.endswith("-documents.json"))
        community_name = next(name for name in archive.namelist() if name.endswith("-communities.json"))
        assert json.loads(archive.read(document_name))[0]["filename"] == "source.md"
        assert json.loads(archive.read(community_name))[0]["members"][0]["name"] in {"设备", "工单"}


def test_every_declared_export_format_can_be_generated(tmp_path: Path):
    exporter, kb_id = seeded_exporter(tmp_path)

    for option in exporter.options(kb_id):
        for format_option in option["formats"]:
            artifact = exporter.build(
                kb_id,
                [{"kind": option["kind"], "format": format_option["value"]}],
            )
            assert artifact.content, f"{option['kind']}.{format_option['value']} is empty"
            assert artifact.filename.endswith(f".{format_option['value']}")
