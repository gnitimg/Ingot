from app.services.chunker import chunk_sections
from app.services.parsers import TextSection


def test_chunker_keeps_metadata_and_limits_size():
    text = "。".join(f"这是第 {index} 句测试文本，包含一些用于切分的内容" for index in range(80))
    chunks = chunk_sections([TextSection(text=text, page_number=3, metadata={"chapter": "测试"})], 260, 40)

    assert len(chunks) > 2
    assert all(len(chunk.content) <= 260 for chunk in chunks)
    assert all(chunk.page_number == 3 for chunk in chunks)
    assert all(chunk.metadata["chapter"] == "测试" for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_chunker_rejects_overlap_larger_than_chunk():
    try:
        chunk_sections([TextSection(text="hello")], 200, 200)
    except ValueError as exc:
        assert "chunk_overlap" in str(exc)
    else:
        raise AssertionError("expected ValueError")

