"""Unit tests for Reciprocal Rank Fusion.

ADAPT: assumed API: reciprocal_rank_fusion(list_of_ranked_id_lists, k=60)
returning ranked results as ids or (id, score) tuples.
"""
import pytest

from backend.retrieval.fusion import reciprocal_rank_fusion  # ADAPT


def _ids(result: list) -> list[str]:
    return [r[0] if isinstance(r, (tuple, list)) else r for r in result]


def _score(result: list, doc: str) -> float:
    for r in result:
        if isinstance(r, (tuple, list)) and r[0] == doc:
            return float(r[1])
    raise AssertionError("scores not returned")


def test_consensus_document_ranks_first() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]])
    assert _ids(fused)[0] == "b"


def test_single_list_preserves_order() -> None:
    assert _ids(reciprocal_rank_fusion([["x", "y", "z"]])) == ["x", "y", "z"]


def test_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == []


def test_no_duplicates_in_output() -> None:
    ids = _ids(reciprocal_rank_fusion([["a", "b"], ["b", "a"], ["a"]]))
    assert len(ids) == len(set(ids)) == 2


def test_union_of_documents() -> None:
    ids = _ids(reciprocal_rank_fusion([["a", "b"], ["c", "d"]]))
    assert set(ids) == {"a", "b", "c", "d"}


def test_known_score_value() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    assert _score(fused, "b") == pytest.approx(1 / 62 + 1 / 61)


def test_smaller_k_increases_top_rank_weight() -> None:
    low = _score(reciprocal_rank_fusion([["a", "b"]], k=1), "a")
    high = _score(reciprocal_rank_fusion([["a", "b"]], k=100), "a")
    assert low > high
