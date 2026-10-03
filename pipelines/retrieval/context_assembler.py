"""Step 5: assemble ranked chunks into a cited context string within a token budget."""
from __future__ import annotations

from dataclasses import dataclass, field

from pipelines.ingestion.chunker import _join, count_tokens, split_sentences

from .models import RetrievalResult

DEFAULT_TOKEN_BUDGET = 4000
MIN_PARTIAL_TOKENS = 100   # don't bother squeezing in a fragment smaller than this


@dataclass
class AssembledContext:
    context: str
    chunks_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    sources: list[dict] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"context": self.context, "chunks_used": self.chunks_used,
                "total_tokens": self.total_tokens, "sources": self.sources}


def _page(chunk: RetrievalResult):
    m = chunk.metadata or {}
    if m.get("page_number") is not None:
        return m["page_number"]
    pages = m.get("page_numbers")
    return pages[0] if pages else None


def _header(i: int, chunk: RetrievalResult) -> str:
    page = _page(chunk)
    name = chunk.filename or "unknown"
    return f"[Source {i} — {name}, page {page}]" if page is not None else f"[Source {i} — {name}]"


class ContextAssembler:
    def __init__(self, token_budget: int = DEFAULT_TOKEN_BUDGET):
        self.token_budget = token_budget

    def _fits(self, blocks: list[str]) -> bool:
        """Measure the real assembled string (token counts are not additive across joins)."""
        return count_tokens("\n\n".join(blocks)) <= self.token_budget

    def assemble(self, chunks: list[RetrievalResult]) -> AssembledContext:
        blocks: list[str] = []
        used: list[RetrievalResult] = []
        truncated = False
        for chunk in chunks:
            header = _header(len(blocks) + 1, chunk)
            block = f"{header}\n{chunk.content}"
            if self._fits(blocks + [block]):
                blocks.append(block)
                used.append(chunk)
                continue
            # Doesn't fit whole: keep as many WHOLE sentences as fit, never cut mid-sentence
            kept = split_sentences(chunk.content)
            while kept and not self._fits(blocks + [f"{header}\n{_join(kept)}"]):
                kept.pop()
            if kept and sum(s.tokens for s in kept) >= MIN_PARTIAL_TOKENS:
                blocks.append(f"{header}\n{_join(kept)}")
                used.append(chunk)
                truncated = True
            break   # budget exhausted; lower-ranked chunks are less relevant anyway

        context = "\n\n".join(blocks)
        return AssembledContext(
            context=context,
            chunks_used=[c.chunk_id for c in used],
            total_tokens=count_tokens(context) if context else 0,
            sources=[{"index": i, "filename": c.filename, "page": _page(c),
                      "chunk_id": c.chunk_id, "document_id": c.document_id}
                     for i, c in enumerate(used, 1)],
            truncated=truncated)
