"""Weighted Reciprocal Rank Fusion: score(d) = sum_i w_i / (k + rank_i(d))."""
from __future__ import annotations

from collections.abc import Sequence


def weighted_rrf(
    ranked_lists: Sequence[Sequence[str]],
    weights: Sequence[float] | None = None,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists. ``weights`` aligns with ``ranked_lists`` (default: equal)."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must match the number of ranked lists")
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = {}
    for ranked, w in zip(ranked_lists, weights, strict=True):
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def dense_sparse_weights(technical_domain: bool, dense_weight: float = 0.6) -> tuple[float, float]:
    """(dense, sparse) weights: 60/40 for technical domains, equal otherwise."""
    if technical_domain:
        return dense_weight, 1.0 - dense_weight
    return 0.5, 0.5
