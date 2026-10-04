"""Offline unit tests for the metric functions (no server needed)."""
import math

from tests.benchmarks.retrieval_benchmark import (
    aggregate, hit_rate_at_k, improvement, mrr_at_k, ndcg_at_k, render_markdown,
)


def test_hit_rate():
    assert hit_rate_at_k(["a", "b", "c"], {"c"}, 5) == 1.0
    assert hit_rate_at_k(["a", "b", "c"], {"c"}, 2) == 0.0


def test_mrr():
    assert mrr_at_k(["x", "a"], {"a"}, 10) == 0.5
    assert mrr_at_k(["x", "y"], {"a"}, 10) == 0.0
    assert mrr_at_k(["x"] * 10 + ["a"], {"a"}, 10) == 0.0  # outside cutoff


def test_ndcg():
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == 1.0
    one = ndcg_at_k(["x", "a"], {"a"}, 10)
    assert math.isclose(one, 1 / math.log2(3))
    assert ndcg_at_k(["x"], {"a"}, 10) == 0.0


def test_aggregate_and_improvement():
    truth = {"q1": {"a"}, "q2": {"b"}}
    m = aggregate({"q1": ["a"], "q2": ["x", "b"]}, truth)
    assert m["MRR@10"] == 0.75 and m["Hit Rate@5"] == 1.0
    assert math.isclose(improvement(0.69, 0.60), 0.15)
    assert improvement(0.5, 0.0) == float("inf")


def test_markdown_pass_fail():
    base = {"Hit Rate@5": .5, "Hit Rate@10": .6, "MRR@10": .40, "NDCG@10": .5}
    better = {**base, "MRR@10": .50}
    md = render_markdown({"dense": base, "hybrid_rerank": better}, 50)
    assert "PASS" in md and "Dense-only" in md
    worse = {**base, "MRR@10": .42}
    assert "FAIL" in render_markdown({"dense": base, "hybrid_rerank": worse}, 50)
