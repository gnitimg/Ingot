from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.parsers import TextSection


@dataclass(slots=True)
class TextChunk:
    content: str
    chunk_index: int
    page_number: int | None
    token_count: int
    metadata: dict[str, str | int]


def _split_long_block(block: str, chunk_size: int) -> list[str]:
    sentences = re.split(r"(?<=[。！？.!?；;])\s*", block)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > chunk_size:
            if current:
                parts.append(current)
                current = ""
            parts.extend(sentence[start : start + chunk_size] for start in range(0, len(sentence), chunk_size))
        elif not current or len(current) + len(sentence) + 1 <= chunk_size:
            current = f"{current} {sentence}".strip()
        else:
            parts.append(current)
            current = sentence
    if current:
        parts.append(current)
    return parts


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    boundary = min((position for position in (tail.find("\n"), tail.find("。"), tail.find(" ")) if position >= 0), default=-1)
    return tail[boundary + 1 :] if boundary >= 0 else tail


def chunk_sections(sections: list[TextSection], chunk_size: int, overlap: int) -> list[TextChunk]:
    if overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")

    output: list[TextChunk] = []
    for section in sections:
        normalized = re.sub(r"[ \t]+", " ", section.text.replace("\r\n", "\n").replace("\r", "\n"))
        blocks: list[str] = []
        for paragraph in re.split(r"\n{2,}|(?<=。)\n", normalized):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            blocks.extend(_split_long_block(paragraph, chunk_size))

        current = ""
        for block in blocks:
            candidate = f"{current}\n\n{block}".strip() if current else block
            if len(candidate) <= chunk_size:
                current = candidate
                continue

            if current:
                output.append(
                    TextChunk(
                        content=current,
                        chunk_index=len(output),
                        page_number=section.page_number,
                        token_count=max(1, len(current) // 3),
                        metadata=dict(section.metadata),
                    )
                )
                current = f"{_overlap_tail(current, overlap)}\n\n{block}".strip()
                if len(current) > chunk_size:
                    current = current[-chunk_size:]
            else:
                current = block

        if current:
            output.append(
                TextChunk(
                    content=current,
                    chunk_index=len(output),
                    page_number=section.page_number,
                    token_count=max(1, len(current) // 3),
                    metadata=dict(section.metadata),
                )
            )
    return output

