from __future__ import annotations

import asyncio
import re

from app.config import Settings
from app.services.ai_client import AIClient
from app.services.parsers import OCRTarget, TextSection


class OCRService:
    def __init__(self, ai: AIClient, settings: Settings):
        self.ai = ai
        self.settings = settings

    @staticmethod
    def _clean_markdown(text: str) -> str:
        value = text.strip()
        if value.startswith("```") and value.endswith("```"):
            value = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
        return value.strip()

    async def recognize(self, targets: list[OCRTarget]) -> tuple[list[TextSection], list[str]]:
        if not targets:
            return [], []
        if not self.settings.ocr_configured:
            fallbacks = [
                TextSection(
                    text=target.fallback_text,
                    page_number=target.page_number,
                    metadata={**target.metadata, "extraction_method": "native_fallback"},
                )
                for target in targets
                if target.fallback_text.strip()
            ]
            return fallbacks, [
                f"{len(targets)} 页需要 OCR，但 OCR 未启用或未配置；已保留可用的原生文本，请检查 OCR_ENABLED、OCR_MODEL 和 API Key"
            ]

        semaphore = asyncio.Semaphore(self.settings.ocr_concurrency)

        async def recognize_one(target: OCRTarget) -> TextSection:
            async with semaphore:
                text = await self.ai.ocr_document(target.content, target.mime_type)
            metadata = {
                **target.metadata,
                "extraction_method": "ocr",
                "ocr_model": self.settings.ocr_model,
            }
            return TextSection(
                text=self._clean_markdown(text),
                page_number=target.page_number,
                metadata=metadata,
            )

        results = await asyncio.gather(
            *(recognize_one(target) for target in targets),
            return_exceptions=True,
        )
        sections: list[TextSection] = []
        warnings: list[str] = []
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                warnings.append(f"第 {target.page_number} 页 OCR 失败：{result}")
                if target.fallback_text.strip():
                    sections.append(
                        TextSection(
                            text=target.fallback_text,
                            page_number=target.page_number,
                            metadata={**target.metadata, "extraction_method": "native_fallback"},
                        )
                    )
            elif result.text.strip():
                sections.append(result)
            else:
                warnings.append(f"第 {target.page_number} 页 OCR 未返回文本")
        return sections, warnings
