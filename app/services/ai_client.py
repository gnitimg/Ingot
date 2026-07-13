from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import numpy as np

from app.config import API_KEY_PLACEHOLDERS, Settings


class AIServiceError(RuntimeError):
    pass


class AIClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def _post_json(
        self, url: str, payload: dict[str, Any], api_key: str, timeout: float
    ) -> dict[str, Any]:
        if api_key.strip() in API_KEY_PLACEHOLDERS:
            raise AIServiceError("模型服务 API Key 未配置，请先运行 init.py 或检查 .env")
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(3):
                try:
                    response = await client.post(url, headers=self._headers(api_key), json=payload)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(1.5 * (2**attempt))
                            continue
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2 and isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
                        await asyncio.sleep(1.5 * (2**attempt))
                        continue
                    break
        detail = str(last_error) if last_error else "未知错误"
        if isinstance(last_error, httpx.HTTPStatusError):
            try:
                detail = last_error.response.json().get("message") or last_error.response.text
            except ValueError:
                detail = last_error.response.text
        raise AIServiceError(f"模型服务请求失败：{detail[:500]}") from last_error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        base_url = self.settings.embedding_base_url.rstrip("/")
        payload = {
            "model": self.settings.embedding_model,
            "input": texts,
            "encoding_format": "float",
        }
        data = await self._post_json(
            f"{base_url}/embeddings",
            payload,
            self.settings.embedding_api_key,
            self.settings.embedding_timeout,
        )
        items = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
        if len(items) != len(texts):
            raise AIServiceError(f"Embedding 返回 {len(items)} 个向量，但请求了 {len(texts)} 个")
        vectors: list[list[float]] = []
        for item in items:
            vector = np.asarray(item.get("embedding", []), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if not vector.size or norm == 0:
                raise AIServiceError("Embedding 服务返回了空向量")
            vectors.append((vector / norm).tolist())
        return vectors

    async def embed_batched(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = self.settings.embedding_batch_size
        for start in range(0, len(texts), batch_size):
            vectors.extend(await self.embed(texts[start : start + batch_size]))
        return vectors

    async def ocr_document(self, content: bytes, mime_type: str) -> str:
        if not self.settings.ocr_configured:
            raise AIServiceError("OCR 未配置，请在 .env 中启用并填写 OCR_MODEL/API Key")
        encoded = base64.b64encode(content).decode("ascii")
        prompt = (
            "<image>\n<|grounding|>Convert the document to markdown."
            if "deepseek-ocr" in self.settings.ocr_model.casefold()
            else "请逐字识别这份文档并转换为 Markdown。保留标题层级、段落、列表、表格、公式和阅读顺序；"
                 "不要总结、解释、改写或补充原文中不存在的信息。"
        )
        payload: dict[str, Any] = {
            "model": self.settings.ocr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{encoded}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 8192,
        }
        data = await self._post_json(
            f"{self.settings.effective_ocr_base_url.rstrip('/')}/chat/completions",
            payload,
            self.settings.effective_ocr_api_key,
            self.settings.ocr_timeout,
        )
        try:
            result = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("OCR 模型返回格式不正确") from exc
        if not isinstance(result, str) or not result.strip():
            raise AIServiceError("OCR 模型没有返回可用文本")
        return result.strip()

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, float | int]]:
        if not documents or not self.settings.rerank_configured:
            return []
        payload = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "return_documents": False,
            "top_n": min(top_n, len(documents)),
        }
        data = await self._post_json(
            f"{self.settings.effective_rerank_base_url.rstrip('/')}/rerank",
            payload,
            self.settings.effective_rerank_api_key,
            self.settings.rerank_timeout,
        )
        results: list[dict[str, float | int]] = []
        for item in data.get("results", []):
            try:
                index = int(item["index"])
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(documents):
                results.append({"index": index, "score": score})
        return results

    async def chat_complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        if not self.settings.chat_model:
            raise AIServiceError("CHAT_MODEL 未配置")
        payload: dict[str, Any] = {
            "model": self.settings.chat_model,
            "messages": messages,
            "stream": False,
            "temperature": self.settings.chat_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.chat_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = await self._post_json(
            f"{self.settings.effective_chat_base_url.rstrip('/')}/chat/completions",
            payload,
            self.settings.effective_chat_api_key,
            self.settings.chat_timeout,
        )
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("对话模型返回格式不正确") from exc

    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        api_key = self.settings.effective_chat_api_key
        if api_key.strip() in API_KEY_PLACEHOLDERS:
            raise AIServiceError("API Key 未配置，请先在 .env 中填写 EMBEDDING_API_KEY 或 CHAT_API_KEY")
        payload = {
            "model": self.settings.chat_model,
            "messages": messages,
            "stream": True,
            "temperature": self.settings.chat_temperature,
            "max_tokens": self.settings.chat_max_tokens,
        }
        url = f"{self.settings.effective_chat_base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.settings.chat_timeout) as client:
                async with client.stream("POST", url, headers=self._headers(api_key), json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload_text = line[5:].strip()
                        if not payload_text or payload_text == "[DONE]":
                            continue
                        try:
                            event = json.loads(payload_text)
                            content = event.get("choices", [{}])[0].get("delta", {}).get("content")
                        except (ValueError, IndexError, TypeError):
                            continue
                        if content:
                            yield str(content)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise AIServiceError(f"对话模型请求失败：{detail}") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError(f"无法连接对话模型：{exc}") from exc
