"""Offline self-test mapped to the Task 38 'Before you move on' checklist.
Run from the repo root:  python -m backend.test_task38
"""
import copy
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.services.stats import compute_analytics, percentile
from backend.task38_demo_app import create_app

LEGAL = {
    "name": "legal-research-v2", "description": "Optimized for legal document analysis",
    "ingestion": {"chunking_strategy": "hierarchical", "chunk_size_tokens": 400,
                  "chunk_overlap_tokens": 80, "extractors_enabled": ["pdf", "docx"]},
    "retrieval": {"dense_k": 30, "sparse_k": 20, "reranker": "cross-encoder", "top_k_after_rerank": 8,
                  "query_expansion": True, "metadata_filters_enabled": True},
    "generation": {"model_routing": {"task_type": "rag_generation", "max_cost_per_call": 0.05},
                   "max_context_tokens": 6000, "temperature": 0.2, "system_prompt_variant": "precise"},
    "evaluation": {"auto_evaluate": True, "training_threshold": 0.82},
}
SUPPORT = {
    "name": "support-fast-v1", "description": "Cheap + fast for customer support",
    "ingestion": {"chunking_strategy": "fixed", "chunk_size_tokens": 256, "chunk_overlap_tokens": 20},
    "retrieval": {"dense_k": 10, "sparse_k": 5, "reranker": None, "top_k_after_rerank": 6},
    "generation": {"max_context_tokens": 3000, "temperature": 0.5, "system_prompt_variant": "friendly"},
}
QUERIES = ["What is the liability clause in the MSA?", "Summarise the termination terms.",
           "Who owns the IP created under the agreement?", "What are the payment terms?",
           "Is there a non-compete obligation?"]


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    assert cond, name


def main():
    c = TestClient(create_app(delay_scale=0.5))

    # 1. Pydantic model rejects unknown keys
    bad = copy.deepcopy(LEGAL); bad["surprise"] = 1
    check("PipelineConfig rejects unknown top-level key", c.post("/pipelines", json=bad).status_code == 422)
    bad = copy.deepcopy(LEGAL); bad["retrieval"]["denze_k"] = 5
    check("PipelineConfig rejects unknown nested key", c.post("/pipelines", json=bad).status_code == 422)

    r = c.post("/pipelines", json=LEGAL); check("POST /pipelines creates (201)", r.status_code == 201, r.json())
    a_id = r.json()["pipeline_id"]
    b_id = c.post("/pipelines", json=SUPPORT).json()["pipeline_id"]
    check("duplicate name -> 409", c.post("/pipelines", json=LEGAL).status_code == 409)

    # 2. updates create new versions, old preserved
    r = c.patch(f"/pipelines/{a_id}", json={"retrieval": {"dense_k": 40}})
    check("PATCH creates version 2", r.json()["version"] == 2 and r.json()["previous_version"] == 1, r.json())
    check("PATCH with unknown key -> 422", c.patch(f"/pipelines/{a_id}", json={"retrieval": {"nope": 1}}).status_code == 422)
    v1 = c.get(f"/pipelines/{a_id}?version=1").json(); v2 = c.get(f"/pipelines/{a_id}").json()
    check("old version preserved unchanged", v1["config"]["retrieval"]["dense_k"] == 30 and v1["version"] == 1)
    check("current version has the update", v2["config"]["retrieval"]["dense_k"] == 40 and v2["version"] == 2)
    check("/versions lists both", len(c.get(f"/pipelines/{a_id}/versions").json()["versions"]) == 2)

    # 3. compare: 5 queries, parallel, scores for both, versions recorded
    print("\nQuery                                           A(ms)  B(ms)  parallel  sequential  evalA  evalB")
    for q in QUERIES:
        res = c.post("/pipelines/compare", json={"query": q, "pipeline_a_id": a_id, "pipeline_b_id": b_id}).json()
        A, B, T = res["pipeline_a"], res["pipeline_b"], res["timing"]
        print(f"{q[:44]:46s} {A['total_latency_ms']:6d} {B['total_latency_ms']:6d} {T['parallel_run_ms']:8d} "
              f"{T['sequential_estimate_ms']:11d} {A['eval_score']!s:>6.6} {B['eval_score']!s:>6.6}")
        assert A["eval_score"] is not None and B["eval_score"] is not None
        assert T["parallel_run_ms"] < T["sequential_estimate_ms"] * 0.8
    check("compare returns eval scores for both pipelines", True)
    check("compare runs both pipelines in parallel (wall < sequential)", True)
    check("compare reports pipeline_version used", A["pipeline_version"] == 2 and B["pipeline_version"] == 1)

    # 4. run history / pipeline_version recorded
    runs = c.get(f"/pipelines/{a_id}/runs?limit=2&offset=1").json()
    check("runs paginated", runs["total"] == 5 and len(runs["items"]) == 2 and runs["offset"] == 1)
    check("pipeline_runs records pipeline_version for every run",
          all(i["pipeline_version"] == 2 for i in c.get(f"/pipelines/{a_id}/runs").json()["items"]))

    # 5. analytics
    an = c.get(f"/pipelines/{a_id}/analytics").json()
    check("analytics has p50/p95/p99 + averages + cost + 30-day sparkline",
          an["total_runs"] == 5 and an["retrieval_latency_ms"]["p95"] is not None
          and an["generation_latency_ms"]["avg"] and an["cost_per_query_usd"] > 0
          and len(an["queries_per_day"]) == 30 and an["queries_per_day"][-1]["count"] == 5
          and an["avg_eval_scores"]["evaluated_runs"] == 5, an["retrieval_latency_ms"])
    rows = [{"retrieval_latency_ms": i, "latency_ms": 0, "created_at": datetime.now(timezone.utc)} for i in range(1, 101)]
    p = compute_analytics(rows)["retrieval_latency_ms"]
    check("p95 computed correctly (1..100 -> 95.05)", p["p95"] == 95.05 and p["p50"] == 50.5 and p["p99"] == 99.01, p)
    check("percentile edge cases", percentile([], 95) is None and percentile([7], 95) == 7.0)

    # 6. list shows last-run metrics
    lst = {p["name"]: p for p in c.get("/pipelines").json()["pipelines"]}
    check("GET /pipelines shows last-run metrics", lst["legal-research-v2"]["last_run"]["eval_score"] is not None)

    # 7. optimizer
    sug = c.get(f"/pipelines/{b_id}/suggestions").json()
    rules = {s["rule"] for s in sug["suggestions"]}
    check("optimizer flags low precision (B has no reranker) and low recall",
          {"low_context_precision", "low_context_recall"} <= rules, sorted(rules))
    check("healthy pipeline gets no precision/recall advice",
          not ({"low_context_precision", "low_context_recall"} & {s["rule"] for s in c.get(f"/pipelines/{a_id}/suggestions").json()["suggestions"]}))

    # 8. soft delete
    r = c.delete(f"/pipelines/{b_id}")
    check("DELETE soft-deletes (status=archived)", r.json()["status"] == "archived")
    check("archived hidden from list, still retrievable",
          all(p["id"] != b_id for p in c.get("/pipelines").json()["pipelines"])
          and c.get(f"/pipelines/{b_id}").json()["status"] == "archived")
    check("archived pipeline cannot be compared/updated",
          c.post("/pipelines/compare", json={"query": "x", "pipeline_a_id": a_id, "pipeline_b_id": b_id}).status_code == 409
          and c.patch(f"/pipelines/{b_id}", json={"description": "x"}).status_code == 409)
    check("unknown pipeline -> 404", c.get("/pipelines/3f2b8c1e-8a4d-4c55-9a43-1b7e2d0c9f11").status_code == 404)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
