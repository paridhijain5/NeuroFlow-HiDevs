#!/usr/bin/env python3
"""Go further: automated hyperparameter search for retrieval, scored by MRR@10.

Grid: dense_k [10,20,30] x sparse_k [10,15] x top_k_after_rerank [5,8,10] x rrf_k [30,60,120]
= 54 combinations; 20 are sampled (seeded) per the task. Every combination is logged to MLflow
and the best one is written to evaluation/suggested_pipeline_config.json.

  python evaluation/hparam_search.py --retriever backend.retrieval.pipeline:retrieve \
      --golden evaluation/retrieval_golden.json

ADAPT: --retriever must point to a callable
    retrieve(query: str, *, dense_k: int, sparse_k: int, top_k_after_rerank: int, rrf_k: int) -> list[str]
returning ranked document/chunk ids. Golden file: [{"question": "...", "expected_ids": ["id1", ...]}].
"""
from __future__ import annotations

import argparse
import importlib
import itertools
import json
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any

GRID: dict[str, list[int]] = {
    "dense_k": [10, 20, 30],
    "sparse_k": [10, 15],
    "top_k_after_rerank": [5, 8, 10],
    "rrf_k": [30, 60, 120],
}


def mrr_at_10(ranked: list[str], expected: set[str]) -> float:
    for rank, doc_id in enumerate(ranked[:10], start=1):
        if doc_id in expected:
            return 1.0 / rank
    return 0.0


def sample_combinations(n: int, seed: int) -> list[dict[str, int]]:
    keys = list(GRID)
    combos = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*GRID.values())]
    return random.Random(seed).sample(combos, min(n, len(combos)))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--retriever", required=True, help="module:function")
    p.add_argument("--golden", default="evaluation/retrieval_golden.json")
    p.add_argument("--n-combos", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="evaluation/suggested_pipeline_config.json")
    a = p.parse_args()

    sys.path.insert(0, os.getcwd())
    module_name, func_name = a.retriever.split(":")
    retrieve = getattr(importlib.import_module(module_name), func_name)
    golden: list[dict[str, Any]] = json.loads(Path(a.golden).read_text())

    try:
        import mlflow

        if os.getenv("MLFLOW_TRACKING_URI"):
            mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment("retrieval_hparam_search")
    except Exception as exc:
        print(f"MLflow unavailable, continuing without it: {exc}")
        mlflow = None  # type: ignore[assignment]

    results: list[dict[str, Any]] = []
    for i, cfg in enumerate(sample_combinations(a.n_combos, a.seed), 1):
        scores = [
            mrr_at_10(retrieve(g["question"], **cfg), set(g["expected_ids"])) for g in golden
        ]
        mrr = statistics.mean(scores)
        results.append({"config": cfg, "mrr_at_10": mrr})
        print(f"[{i}/{a.n_combos}] MRR@10={mrr:.4f} {cfg}")
        if mlflow is not None:
            with mlflow.start_run(run_name=f"combo_{i:02d}"):
                mlflow.log_params(cfg)
                mlflow.log_metric("mrr_at_10", mrr)

    best = max(results, key=lambda r: r["mrr_at_10"])
    Path(a.out).write_text(json.dumps(
        {"suggested_pipeline_config": best["config"], "mrr_at_10": best["mrr_at_10"], "all_results": results},
        indent=2,
    ))
    print(f"\nBest: {best}\nWrote {a.out}  (apply via env: DENSE_K, SPARSE_K, TOP_K_AFTER_RERANK, RRF_K)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
