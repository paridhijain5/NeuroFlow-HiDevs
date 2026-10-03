"""Percentiles + pipeline analytics computed from run rows (shared by every repo implementation)."""
from __future__ import annotations

import math, os
from datetime import datetime, timedelta, timezone
from typing import Iterable

METRICS = ("faithfulness", "answer_relevance", "context_precision", "context_recall", "overall_score")

# ADAPT: placeholder prices in USD per 1K tokens - ideally read from your ModelRouter per model_used.
PRICE_INPUT_PER_1K = float(os.getenv("PRICE_INPUT_PER_1K", "0.003"))
PRICE_OUTPUT_PER_1K = float(os.getenv("PRICE_OUTPUT_PER_1K", "0.015"))


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (same as Postgres percentile_cont). q in [0, 100]."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return float(s[lo]) if lo == hi else float(s[lo] + (s[hi] - s[lo]) * (k - lo))


def _mean(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(x, n)


def latency_stats(values: Iterable[float | None]) -> dict:
    v = [float(x) for x in values if x is not None]
    return {"avg": _r(_mean(v)), "p50": _r(percentile(v, 50)), "p95": _r(percentile(v, 95)),
            "p99": _r(percentile(v, 99))}


def avg_scores(rows: list[dict]) -> dict:
    out = {}
    evaluated = [r for r in rows if r.get("overall_score") is not None]
    for m in METRICS:
        out[m] = _r(_mean([r[m] for r in evaluated if r.get(m) is not None]), 4)
    out["evaluated_runs"] = len(evaluated)
    return out


def _to_date(ts: datetime):
    return (ts.astimezone(timezone.utc) if ts.tzinfo else ts).date()


def compute_analytics(rows: list[dict], now: datetime | None = None, days: int = 30) -> dict:
    now = now or datetime.now(timezone.utc)
    cost = [(r["input_tokens"] * PRICE_INPUT_PER_1K + r["output_tokens"] * PRICE_OUTPUT_PER_1K) / 1000
            for r in rows if r.get("input_tokens") is not None and r.get("output_tokens") is not None]
    today = _to_date(now)
    buckets = {today - timedelta(days=i): 0 for i in range(days - 1, -1, -1)}
    for r in rows:
        if r.get("created_at") is not None:
            d = _to_date(r["created_at"])
            if d in buckets:
                buckets[d] += 1
    return {
        "total_runs": len(rows),
        "retrieval_latency_ms": latency_stats(r.get("retrieval_latency_ms") for r in rows),
        "generation_latency_ms": {"avg": latency_stats(r.get("latency_ms") for r in rows)["avg"]},
        "avg_eval_scores": avg_scores(rows),
        "cost_per_query_usd": _r(_mean(cost), 6),
        "total_cost_usd": _r(sum(cost), 6),
        "queries_per_day": [{"date": d.isoformat(), "count": c} for d, c in buckets.items()],
    }
