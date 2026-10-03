"""Chunking: fixed_size, semantic, hierarchical, plus automatic strategy selection."""
from __future__ import annotations

import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Awaitable, Callable

import numpy as np

from .models import ExtractedPage

log = logging.getLogger(__name__)

TARGET_TOKENS = 512
OVERLAP_TOKENS = 64
BOUNDARY_TOLERANCE = 0.10      # cut at a sentence boundary within +-10% of target
SEMANTIC_THRESHOLD = 0.7
MIN_SEMANTIC_TOKENS = 64
PARENT_MAX_TOKENS = 1500
LONG_PDF_PAGES = 50

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


# --------------------------------------------------------------------------- tokens
@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # offline / download blocked
        log.warning("tiktoken unavailable (%s); using ~4 chars/token estimate", exc)
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------- sentences
@dataclass
class Sent:
    text: str
    tokens: int
    page: int
    sep: str = " "


@dataclass
class Chunk:
    id: str
    content: str
    chunk_index: int
    token_count: int
    metadata: dict = field(default_factory=dict)


_SPLIT = re.compile(r'((?<=[.!?])[ \t]+(?=[A-Z0-9"\'(\[“‘])|\n+)')


def split_sentences(text: str, page: int = 0) -> list[Sent]:
    parts = _SPLIT.split(text)
    out: list[Sent] = []
    for i in range(0, len(parts), 2):
        t = parts[i].strip()
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if "\n" in sep:
            sep = "\n\n" if sep.count("\n") >= 2 else "\n"
        elif sep:
            sep = " "
        if not t:
            if out and sep:
                out[-1].sep = sep
            continue
        out.append(Sent(t, count_tokens(t), page, sep))
    return out


def _join(sents: list[Sent]) -> str:
    return "".join(s.text + s.sep for s in sents[:-1]) + sents[-1].text


def _explode(s: Sent, limit: int) -> list[Sent]:
    """A single sentence longer than `limit` is split on words (last resort)."""
    if s.tokens <= limit:
        return [s]
    n_pieces = math.ceil(s.tokens / limit)
    words = s.text.split()
    if len(words) >= n_pieces:
        per = math.ceil(len(words) / n_pieces)
        texts = [" ".join(words[i:i + per]) for i in range(0, len(words), per)]
    else:  # one giant "word" (e.g. base64): split by characters
        size = math.ceil(len(s.text) / n_pieces)
        texts = [s.text[i:i + size] for i in range(0, len(s.text), size)]
    pieces = [Sent(t, count_tokens(t), s.page, " ") for t in texts]
    pieces[-1].sep = s.sep
    return pieces


# --------------------------------------------------------------------------- fixed size
def pack_fixed(sents: list[Sent], target: int = TARGET_TOKENS,
               overlap: int = OVERLAP_TOKENS) -> list[list[Sent]]:
    """Greedy packing that only cuts at sentence boundaries within +-10% of `target`."""
    sents = [p for s in sents for p in _explode(s, target)]
    n = len(sents)
    hi = target * (1 + BOUNDARY_TOLERANCE)
    lo = target * (1 - BOUNDARY_TOLERANCE)
    groups: list[list[Sent]] = []
    start = 0
    while start < n:
        cum, ends, overflow = 0, [], False
        for j in range(start, n):
            cum += sents[j].tokens
            if cum > hi:
                overflow = True
                break
            ends.append((j + 1, cum))
        if not overflow:
            end = n
        else:
            window = [(e, c) for e, c in ends if c >= lo]
            if window:
                end = min(window, key=lambda x: abs(x[1] - target))[0]
            elif ends:
                end = ends[-1][0]
            else:
                end = start + 1
        group = sents[start:end]
        groups.append(group)
        if end >= n:
            break
        k, tot = 0, 0
        for s in reversed(group):  # sentence-aligned overlap
            if tot + s.tokens > overlap:
                break
            tot += s.tokens
            k += 1
        k = min(k, len(group) - 1)
        start = max(end - k, start + 1)
    return groups


# --------------------------------------------------------------------------- tables
_SEPARATOR_ROW = re.compile(r"^\|?\s*:?-{3,}")


def table_chunks_text(content: str, target: int = TARGET_TOKENS) -> list[str]:
    """Split a table at row boundaries, repeating the markdown header in each chunk."""
    lines = content.split("\n")
    header: list[str] = []
    if len(lines) >= 2 and _SEPARATOR_ROW.match(lines[1].strip()):
        header, lines = lines[:2], lines[2:]
    header_tokens = count_tokens("\n".join(header)) if header else 0
    out, cur, cur_tokens = [], [], header_tokens
    for line in lines:
        t = count_tokens(line)
        if cur and cur_tokens + t > target:
            out.append("\n".join(header + cur))
            cur, cur_tokens = [], header_tokens
        cur.append(line)
        cur_tokens += t
    if cur or not out:
        out.append("\n".join(header + cur))
    return out


# --------------------------------------------------------------------------- semantic
def _cos(a: np.ndarray, b: np.ndarray) -> float:
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / d if d else 0.0


async def semantic_groups(sents: list[Sent], embed_fn: EmbedFn, threshold: float = SEMANTIC_THRESHOLD,
                          window: int = 1, max_tokens: int = TARGET_TOKENS) -> list[list[Sent]]:
    if len(sents) < 2:
        return [sents] if sents else []
    vecs = np.array(await embed_fn([s.text for s in sents]), dtype=float)
    groups: list[list[Sent]] = []
    cur = [sents[0]]
    for i in range(len(sents) - 1):
        a = vecs[max(0, i - window + 1): i + 1].mean(axis=0)
        b = vecs[i + 1: i + 1 + window].mean(axis=0)
        if _cos(a, b) < threshold:
            groups.append(cur)
            cur = []
        cur.append(sents[i + 1])
    groups.append(cur)

    merged: list[list[Sent]] = []  # fold tiny groups into their predecessor
    for g in groups:
        tokens = sum(s.tokens for s in g)
        if merged and tokens < MIN_SEMANTIC_TOKENS and \
                sum(s.tokens for s in merged[-1]) + tokens <= max_tokens:
            merged[-1].extend(g)
        else:
            merged.append(g)

    out: list[list[Sent]] = []  # split oversized topics
    for g in merged:
        if sum(s.tokens for s in g) > max_tokens * (1 + BOUNDARY_TOLERANCE):
            out.extend(pack_fixed(g))
        else:
            out.append(g)
    return out


# --------------------------------------------------------------------------- strategy
_HEADING_LEVEL = re.compile(r"^h[1-6]$")


def select_strategy(pages: list[ExtractedPage], source_type: str) -> str:
    if pages and all(p.content_type == "table" for p in pages):
        return "fixed_size"  # tables always use fixed_size
    if source_type == "docx" and any(_HEADING_LEVEL.match(str(p.metadata.get("level", ""))) for p in pages):
        return "hierarchical"
    if source_type == "pdf" and max((p.page_number for p in pages), default=0) > LONG_PDF_PAGES:
        return "semantic"
    return "fixed_size"


# --------------------------------------------------------------------------- assembly
def _make_chunk(content: str, meta: dict, chunk_id: str | None = None) -> Chunk:
    return Chunk(id=chunk_id or str(uuid.uuid4()), content=content, chunk_index=0,
                 token_count=count_tokens(content), metadata=meta)


def _group_chunk(group: list[Sent], page_meta: dict[int, dict], content_type: str, strategy: str) -> Chunk:
    pages = sorted({s.page for s in group})
    meta = dict(page_meta.get(pages[0], {}))
    meta.update({"page_numbers": pages, "page_number": pages[0],
                 "content_type": content_type, "chunking_strategy": strategy})
    return _make_chunk(_join(group), meta)


def _table_chunks(page: ExtractedPage, strategy: str, extra: dict | None = None) -> list[Chunk]:
    out = []
    for text in table_chunks_text(page.content):
        meta = {**page.metadata, "page_numbers": [page.page_number], "page_number": page.page_number,
                "content_type": "table", "chunking_strategy": strategy, **(extra or {})}
        out.append(_make_chunk(text, meta))
    return out


def _hierarchical(pages: list[ExtractedPage]) -> list[Chunk]:
    """Top-level (h1) section -> parent chunk; sub-sections/tables -> child chunks."""
    groups: dict[str | None, list[ExtractedPage]] = {}
    for p in pages:
        path = p.metadata.get("heading_path") or []
        groups.setdefault(path[0] if path else None, []).append(p)

    chunks: list[Chunk] = []
    for top, group in groups.items():
        parent: Chunk | None = None
        children: list[Chunk] = []
        remaining = list(group)
        if top is not None:
            own = next((p for p in group if p.metadata.get("level") == "h1" and p.content_type != "table"), None)
            if own is not None:
                remaining.remove(own)
                sents = split_sentences(own.content, own.page_number)
                head: list[Sent] = []
                used = 0
                for s in sents:  # parent holds up to PARENT_MAX_TOKENS of the section's own text
                    if head and used + s.tokens > PARENT_MAX_TOKENS:
                        break
                    head.append(s)
                    used += s.tokens
                text = _join(head)
                titles = [p.metadata.get("section") for p in group
                          if p.metadata.get("level") not in ("h1", None) and p.content_type != "table"]
                if used < 30 and titles:
                    text += "\nSubsections: " + "; ".join(dict.fromkeys(t for t in titles if t))
                parent = _make_chunk(text, {**own.metadata, "page_numbers": [own.page_number],
                                            "page_number": own.page_number, "content_type": "text",
                                            "chunking_strategy": "hierarchical", "chunk_role": "parent",
                                            "is_parent": True})
                leftover = sents[len(head):]
                for g in pack_fixed(leftover):
                    children.append(_group_chunk(g, {own.page_number: own.metadata}, "text", "hierarchical"))
        for p in remaining:
            if p.content_type == "table":
                children.extend(_table_chunks(p, "hierarchical"))
            else:
                for g in pack_fixed(split_sentences(p.content, p.page_number)):
                    children.append(_group_chunk(g, {p.page_number: p.metadata}, "text", "hierarchical"))
        for c in children:
            c.metadata["chunk_role"] = "child" if parent else "standalone"
            if parent:
                c.metadata["parent_chunk_id"] = parent.id
        if parent:
            parent.metadata["child_chunk_ids"] = [c.id for c in children]
            chunks.append(parent)
        chunks.extend(children)
    return chunks


async def chunk_pages(pages: list[ExtractedPage], source_type: str, embed_fn: EmbedFn | None = None,
                      strategy: str | None = None) -> tuple[list[Chunk], str]:
    strategy = strategy or select_strategy(pages, source_type)
    if strategy == "semantic" and embed_fn is None:
        raise ValueError("semantic chunking needs an embed_fn")

    if strategy == "hierarchical":
        chunks = _hierarchical(pages)
    else:
        chunks = []
        page_meta = {p.page_number: p.metadata for p in pages}
        stream: list[Sent] = []
        stream_type = "text"

        async def flush():
            nonlocal stream
            if stream:
                groups = (await semantic_groups(stream, embed_fn) if strategy == "semantic"
                          else pack_fixed(stream))
                chunks.extend(_group_chunk(g, page_meta, stream_type, strategy) for g in groups)
                stream = []

        for p in pages:
            if p.content_type == "table":  # tables are always chunked by rows
                await flush()
                chunks.extend(_table_chunks(p, "fixed_size"))
            else:
                if stream and p.content_type != stream_type:
                    await flush()
                stream_type = p.content_type
                stream.extend(split_sentences(p.content, p.page_number))
        await flush()

    for i, c in enumerate(chunks):
        c.chunk_index = i
        c.metadata["source_type"] = source_type
    return chunks, strategy
