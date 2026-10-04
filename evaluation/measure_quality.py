#!/usr/bin/env python3
"""Runs the evaluation suite and writes a metrics file compared against the sprint targets.

  # 1) BEFORE any change:
  python evaluation/measure_quality.py --out evaluation/quality_baseline.json --no-fail
  # 3) AFTER improvements:
  python evaluation/measure_quality.py --out evaluation/quality_final.json

Retrieval metrics are read from evaluation/retrieval_results.json (written by retrieval_eval.py),
generation metrics + P95 latency from evaluation/generation_results.json.
Values are always taken from real eval output: nothing is hardcoded.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGETS: dict[str, tuple[str, float]] = {
    "retrieval_hit_rate_at_10": (">", 0.80),
    "retrieval_mrr_at_10": (">", 0.60),
    "faithfulness": (">", 0.78),
    "answer_relevance": (">", 0.75),
    "context_precision": (">", 0.72),
    "overall_eval_score": (">", 0.75),
    "p95_latency_s": ("<", 4.0),
}
HIT_KEYS = ["hit_rate_at_10", "hit_rate@10", "hit_rate", "hit_rate_10"]
MRR_KEYS = ["mrr_at_10", "mrr@10", "mrr", "mrr_10"]


def _first(d: dict[str, Any], keys: list[str]) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            return float(d[k])
    raise KeyError(f"none of {keys} found in {sorted(d)}; edit HIT_KEYS/MRR_KEYS in this script")


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--api-url", default="")
    p.add_argument("--skip-run", action="store_true", help="reuse existing result files")
    p.add_argument("--no-fail", action="store_true", help="exit 0 even if targets are missed")
    a = p.parse_args()

    if not a.skip_run:
        _run([sys.executable, "evaluation/retrieval_eval.py"])
        gen = [sys.executable, "evaluation/generation_eval.py"]
        _run(gen + (["--api-url", a.api_url] if a.api_url else []))

    retrieval = json.loads(Path("evaluation/retrieval_results.json").read_text())
    generation = json.loads(Path("evaluation/generation_results.json").read_text())
    metrics = {
        "retrieval_hit_rate_at_10": _first(retrieval, HIT_KEYS),
        "retrieval_mrr_at_10": _first(retrieval, MRR_KEYS),
        "faithfulness": float(generation["faithfulness"]),
        "answer_relevance": float(generation["answer_relevance"]),
        "context_precision": float(generation["context_precision"]),
        "overall_eval_score": float(generation["overall"]),
        "p95_latency_s": float(generation["p95_latency_s"]),
    }
    report: dict[str, Any] = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": metrics,
        "targets": {k: f"{op} {v}" for k, (op, v) in TARGETS.items()},
        "passed": {},
    }
    for name, (op, target) in TARGETS.items():
        value = metrics[name]
        report["passed"][name] = value > target if op == ">" else value < target
        print(f"{'PASS' if report['passed'][name] else 'FAIL'}  {name:28s} {value:.3f}  (target {op} {target})")
    report["all_targets_met"] = all(report["passed"].values())
    Path(a.out).write_text(json.dumps(report, indent=2))
    print(f"\nWrote {a.out}  all_targets_met={report['all_targets_met']}")
    return 0 if (report["all_targets_met"] or a.no_fail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
