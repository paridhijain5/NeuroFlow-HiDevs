"""Parent-child chunking: embed small child chunks, but retrieve the larger parent for context.

Token counts use whitespace tokens as a cheap proxy (swap ``count_tokens`` for tiktoken if desired).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ParentChunk:
    id: str
    text: str


@dataclass(frozen=True)
class ChildChunk:
    id: str
    text: str
    parent_id: str


def count_tokens(text: str) -> int:
    return len(text.split())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]


def pack_sentences(text: str, max_tokens: int) -> list[str]:
    """Greedy sentence-boundary packing; oversized sentences are split by words."""
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in _sentences(text):
        n = count_tokens(sentence)
        if n > max_tokens:
            if current:
                pieces.append(" ".join(current))
                current, size = [], 0
            words = sentence.split()
            pieces.extend(
                " ".join(words[i : i + max_tokens]) for i in range(0, len(words), max_tokens)
            )
            continue
        if size + n > max_tokens and current:
            pieces.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += n
    if current:
        pieces.append(" ".join(current))
    return pieces


def build_parent_child(
    text: str,
    doc_id: str = "doc",
    parent_tokens: int = 512,
    child_tokens: int = 128,
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    if child_tokens > parent_tokens:
        raise ValueError("child_tokens must be <= parent_tokens")
    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    for p_idx, p_text in enumerate(pack_sentences(text, parent_tokens)):
        parent = ParentChunk(id=f"{doc_id}:p{p_idx}", text=p_text)
        parents.append(parent)
        for c_idx, c_text in enumerate(pack_sentences(p_text, child_tokens)):
            child_id = f"{parent.id}:c{c_idx}"
            children.append(ChildChunk(id=child_id, text=c_text, parent_id=parent.id))
    return parents, children


def expand_to_parents(
    child_ids: Sequence[str],
    children: Mapping[str, ChildChunk],
    parents: Mapping[str, ParentChunk],
) -> list[ParentChunk]:
    """Map retrieved child ids to unique parents, preserving best-first order."""
    seen: set[str] = set()
    result: list[ParentChunk] = []
    for cid in child_ids:
        child = children.get(cid)
        if child is None or child.parent_id in seen:
            continue
        seen.add(child.parent_id)
        result.append(parents[child.parent_id])
    return result
