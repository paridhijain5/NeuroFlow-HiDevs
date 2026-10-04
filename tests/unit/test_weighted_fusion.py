import pytest

from backend.retrieval.weighted_fusion import dense_sparse_weights, weighted_rrf


def test_equal_weights_consensus_wins() -> None:
    fused = weighted_rrf([["a", "b", "c"], ["b", "c", "a"]])
    assert fused[0][0] == "b"


def test_dense_weight_changes_winner() -> None:
    dense, sparse = ["a", "b"], ["b", "a"]
    assert weighted_rrf([dense, sparse], [0.9, 0.1])[0][0] == "a"
    assert weighted_rrf([dense, sparse], [0.1, 0.9])[0][0] == "b"


def test_known_score() -> None:
    fused = dict(weighted_rrf([["a"]], [0.6], k=60))
    assert fused["a"] == pytest.approx(0.6 / 61)


def test_weight_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        weighted_rrf([["a"], ["b"]], [1.0])


def test_invalid_k_raises() -> None:
    with pytest.raises(ValueError):
        weighted_rrf([["a"]], k=0)


def test_technical_domain_weights() -> None:
    assert dense_sparse_weights(True) == pytest.approx((0.6, 0.4))
    assert dense_sparse_weights(False) == (0.5, 0.5)
