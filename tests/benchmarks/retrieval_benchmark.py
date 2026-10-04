"""Retrieval benchmark: dense vs sparse vs hybrid (RRF) vs hybrid+rerank.

Step 1 (once, after ingesting the fixture PDF):
    python -m tests.benchmarks.retrieval_benchmark --build-ground-truth
  -> resolves tests/benchmarks/questions.json keywords to relevant chunk IDs of YOUR
     ingested chunks and writes tests/benchmarks/ground_truth.json (commit it).
Step 2:
    python -m tests.benchmarks.retrieval_benchmark
  -> writes tests/benchmarks/retrieval_benchmark_results.md, exit 1 if
     Hybrid+Reranked MRR@10 is not >= 15% above Dense-only.
Also collected by `pytest tests/` (skips if ground truth / server missing).
"""
import argparse
import asyncio
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests import config
from tests.helpers import api, ingest_test_document, server_is_up, wait_for_status

BENCH_DIR = Path(__file__).resolve().parent
QUESTIONS = BENCH_DIR / "questions.json"
GROUND_TRUTH = BENCH_DIR / "ground_truth.json"
RESULTS_MD = BENCH_DIR / "retrieval_benchmark_results.md"
REQUIRED_IMPROVEMENT = 0.15
LABELS = {
    "dense": "Dense-only", "sparse": "Sparse-only",
    "hybrid": "Hybrid (RRF)", "hybrid_rerank": "Hybrid + Reranked",
}


# ------------------------------------------------------------ metrics (binary relevance)
def hit_rate_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(c in relevant for c in ranked[:k]) else 0.0


def mrr_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    for i, c in enumerate(ranked[:k], start=1):
        if c in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, c in enumerate(ranked[:k], start=1) if c in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def aggregate(rankings: dict[str, list[str]], truth: dict[str, set[str]]) -> dict:
    n = len(truth)
    ids = list(truth)
    return {
        "Hit Rate@5": sum(hit_rate_at_k(rankings[q], truth[q], 5) for q in ids) / n,
        "Hit Rate@10": sum(hit_rate_at_k(rankings[q], truth[q], 10) for q in ids) / n,
        "MRR@10": sum(mrr_at_k(rankings[q], truth[q], 10) for q in ids) / n,
        "NDCG@10": sum(ndcg_at_k(rankings[q], truth[q], 10) for q in ids) / n,
    }


def improvement(candidate: float, baseline: float) -> float:
    return float("inf") if baseline == 0 and candidate > 0 else (
        0.0 if baseline == 0 else (candidate - baseline) / baseline)


def render_markdown(results: dict[str, dict], n_questions: int) -> str:
    cols = ["Hit Rate@5", "Hit Rate@10", "MRR@10", "NDCG@10"]
    lines = [
        "# Retrieval Benchmark Results", "",
        f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} - "
        f"{n_questions} questions, ground truth = relevant chunk IDs._", "",
        "| Strategy | " + " | ".join(cols) + " |",
        "|---|" + "---|" * len(cols),
    ]
    for mode, m in results.items():
        lines.append(f"| {LABELS.get(mode, mode)} | " + " | ".join(f"{m[c]:.3f}" for c in cols) + " |")
    dense, best = results.get("dense"), results.get("hybrid_rerank")
    if dense and best:
        imp = improvement(best["MRR@10"], dense["MRR@10"])
        ok = imp >= REQUIRED_IMPROVEMENT
        lines += ["", f"**Hybrid+Reranked vs Dense-only on MRR@10: {imp:+.1%}** "
                      f"(required >= {REQUIRED_IMPROVEMENT:.0%}) - {'PASS' if ok else 'FAIL'}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ API-facing
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("\u2010", "-").replace("\u2011", "-").replace("\u2013", "-"))


async def retrieve(query: str, mode: str, k: int = 10) -> list[str]:
    r = await api("POST", config.RETRIEVE, json={"query": query, "mode": mode, "top_k": k})
    r.raise_for_status()
    return [x[config.CHUNK_ID_KEY] for x in r.json()["results"]]


async def build_ground_truth() -> dict:
    doc_id = await ingest_test_document(str(config.TEST_DOC))
    await wait_for_status(doc_id, "complete", timeout=120)
    r = await api("GET", config.DOC_CHUNKS.format(doc_id=doc_id))
    r.raise_for_status()
    data = r.json()
    chunks = data["chunks"] if isinstance(data, dict) else data
    texts = {c[config.CHUNK_ID_KEY]: _norm(c[config.CHUNK_TEXT_KEY]) for c in chunks}

    out, dropped = [], []
    for q in json.loads(QUESTIONS.read_text()):
        rel = [cid for cid, t in texts.items() if all(_norm(k) in t for k in q["keywords"])]
        (out if rel else dropped).append({**q, "relevant_chunk_ids": rel} if rel else q["id"])
    GROUND_TRUTH.write_text(json.dumps({"document_id": doc_id, "questions": out}, indent=2))
    print(f"ground truth: {len(out)} questions kept, {len(dropped)} dropped (no matching chunk): {dropped}")
    if len(out) < 50:
        print("WARNING: fewer than 50 questions have ground truth - edit keywords in questions.json")
    return {"kept": len(out), "dropped": dropped}


async def run_benchmark() -> tuple[dict, int, bool]:
    gt = json.loads(GROUND_TRUTH.read_text())["questions"]
    truth = {q["id"]: set(q["relevant_chunk_ids"]) for q in gt}
    results = {}
    for mode in config.RETRIEVAL_MODES:
        rankings = {q["id"]: await retrieve(q["question"], mode, 10) for q in gt}
        results[mode] = aggregate(rankings, truth)
        print(f"{LABELS[mode]:<20}", {k: round(v, 3) for k, v in results[mode].items()})
    RESULTS_MD.write_text(render_markdown(results, len(gt)))
    imp = improvement(results["hybrid_rerank"]["MRR@10"], results["dense"]["MRR@10"])
    return results, len(gt), imp >= REQUIRED_IMPROVEMENT


async def test_hybrid_reranked_beats_dense():
    if not GROUND_TRUTH.exists():
        pytest.skip("ground_truth.json missing: run --build-ground-truth first")
    if not await server_is_up():
        pytest.skip(f"NeuroFlow not reachable at {config.BASE_URL}")
    results, _, ok = await run_benchmark()
    assert ok, (
        f"Hybrid+Reranked MRR@10 {results['hybrid_rerank']['MRR@10']:.3f} is not >=15% above "
        f"Dense-only {results['dense']['MRR@10']:.3f}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-ground-truth", action="store_true")
    args = ap.parse_args()
    if args.build_ground_truth:
        asyncio.run(build_ground_truth())
    else:
        _, _, passed = asyncio.run(run_benchmark())
        print("PASS" if passed else "FAIL: Hybrid+Reranked must beat Dense-only on MRR@10 by >=15%")
        sys.exit(0 if passed else 1)
