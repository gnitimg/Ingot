from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.config import Settings
from app.services.ocr import OCRService
from app.services.parsers import OCRTarget, parse_document


class FakeAI:
    async def ocr_document(self, content: bytes, mime_type: str) -> str:
        assert content
        assert mime_type == "image/png"
        return "```markdown\n# OCR 标题\n\n识别正文\n```"


def test_image_is_prepared_as_ocr_target(tmp_path: Path):
    path = tmp_path / "scan.jpg"
    image = Image.new("RGB", (120, 60), "white")
    image.save(path, format="JPEG")

    parsed = parse_document(path, "scan.jpg")

    assert parsed.page_count == 1
    assert parsed.sections == []
    assert len(parsed.ocr_targets) == 1
    assert parsed.ocr_targets[0].mime_type == "image/png"
    assert parsed.ocr_targets[0].content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_ocr_service_keeps_page_metadata():
    settings = Settings(
        _env_file=None,
        embedding_api_key="test-key",
        ocr_enabled=True,
        ocr_model="test-ocr",
    )
    buffer = BytesIO()
    Image.new("RGB", (10, 10), "white").save(buffer, format="PNG")
    target = OCRTarget(buffer.getvalue(), "image/png", 7, {"source": "scan"})

    sections, warnings = await OCRService(FakeAI(), settings).recognize([target])  # type: ignore[arg-type]

    assert warnings == []
    assert sections[0].text == "# OCR 标题\n\n识别正文"
    assert sections[0].page_number == 7
    assert sections[0].metadata["extraction_method"] == "ocr"
    assert sections[0].metadata["ocr_model"] == "test-ocr"


@pytest.mark.asyncio
async def test_ocr_service_reports_unconfigured_state():
    settings = Settings(_env_file=None, embedding_api_key="***", ocr_enabled=True)
    sections, warnings = await OCRService(FakeAI(), settings).recognize(  # type: ignore[arg-type]
        [OCRTarget(b"png", "image/png", 1)]
    )
    assert sections == []
    assert "OCR 未启用或未配置" in warnings[0]

