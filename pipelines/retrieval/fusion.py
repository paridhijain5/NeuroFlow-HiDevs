"""Step 3: Reciprocal Rank Fusion."""
from __future__ import annotations

from .models import RetrievalResult


def reciprocal_rank_fusion(result_lists: list[list[RetrievalResult]], k: int = 60) -> list[RetrievalResult]:
    """score(chunk) = sum over lists of 1 / (k + rank), rank starting at 1.

    Chunks present in several lists accumulate score, so agreement between dense,
    sparse and metadata retrieval is rewarded. Ties keep first-seen order.
    """
    scores: dict[str, float] = {}
    best: dict[str, RetrievalResult] = {}
    strategies: dict[str, list[str]] = {}
    order: dict[str, int] = {}
    for results in result_lists:
        for rank, r in enumerate(results, start=1):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank)
            best.setdefault(r.chunk_id, r)
            order.setdefault(r.chunk_id, len(order))
            for s in r.strategies:
                if s not in strategies.setdefault(r.chunk_id, []):
                    strategies[r.chunk_id].append(s)
    fused = [best[cid].copy(score=sc, rrf_score=sc, strategies=strategies.get(cid, []))
             for cid, sc in scores.items()]
    fused.sort(key=lambda r: (-r.score, order[r.chunk_id]))
    return fused
