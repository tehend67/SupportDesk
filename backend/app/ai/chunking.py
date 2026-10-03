import re
from typing import List

_WHITESPACE = re.compile(r"\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…。！？])\s+")
_PARAGRAPH = re.compile(r"\n\s*\n")


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()


def estimate_tokens(text: str) -> int:
    normalized = normalize(text)
    if not normalized:
        return 0
    return max(1, len(normalized) // 4)


def split_sentences(text: str) -> List[str]:
    normalized = normalize(text)
    if not normalized:
        return []
    result: List[str] = []
    for paragraph in _PARAGRAPH.split(normalized) if "\n" in text else [normalized]:
        for sentence in _SENTENCE_BOUNDARY.split(paragraph):
            sentence = sentence.strip()
            if sentence:
                result.append(sentence)
    return result or [normalized]


def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> List[str]:
    normalized = normalize(text)
    if not normalized:
        return []
    max_chars = max(120, max_chars)
    overlap = max(0, min(overlap, max_chars // 3))
    if len(normalized) <= max_chars:
        return [normalized]

    chunks: List[str] = []
    current = ""
    for sentence in split_sentences(text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        tail = current[-overlap:] if overlap and current else ""
        current = f"{tail} {sentence}".strip() if tail else sentence
        while len(current) > max_chars:
            chunks.append(current[:max_chars].strip())
            current = current[max_chars - overlap:].strip()

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]
