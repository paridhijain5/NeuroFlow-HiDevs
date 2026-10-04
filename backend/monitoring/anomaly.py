"""'Go further': quality anomaly detection. Rolling mean/std of the overall evaluation score over 7 days; if the
latest score is > 2 std below the mean, fetch config suggestions and annotate the Grafana dashboard."""
from __future__ import annotations

import asyncio, json, math, os, time, urllib.request
from dataclasses import dataclass, asdict
from typing import Awaitable, Callable

MIN_SAMPLES = 10
SIGMAS = 2.0


@dataclass
class Anomaly:
    pipeline_id: str
    latest: float
    mean: float
    std: float
    threshold: float
    samples: int


def detect_anomaly(pipeline_id: str, scores_oldest_first: list[float], *, min_samples: int = MIN_SAMPLES,
                   sigmas: float = SIGMAS) -> Anomaly | None:
    """Baseline = every score EXCEPT the latest (so a bad point doesn't dilute its own baseline)."""
    if len(scores_oldest_first) < min_samples + 1:
        return None
    *history, latest = scores_oldest_first
    mean = sum(history) / len(history)
    std = math.sqrt(sum((x - mean) ** 2 for x in history) / len(history))
    if std == 0:
        return None
    threshold = mean - sigmas * std
    if latest < threshold:
        return Anomaly(str(pipeline_id), latest, mean, std, threshold, len(history))
    return None


def annotation_payload(a: Anomaly, suggestions: list[dict], now_ms: int | None = None) -> dict:
    lines = [f"Quality anomaly on pipeline {a.pipeline_id}: overall {a.latest:.3f} vs mean {a.mean:.3f} "
             f"(2σ floor {a.threshold:.3f})"]
    lines += [f"• suggest {s['path']}: {s['current']} → {s['suggested']} ({s['rationale']})" for s in suggestions]
    return {"time": now_ms or int(time.time() * 1000), "tags": ["neuroflow", "anomaly", str(a.pipeline_id)],
            "text": "\n".join(lines)}


async def post_grafana_annotation(payload: dict, url: str | None = None, token: str | None = None) -> None:
    url = (url or os.getenv("GRAFANA_URL", "http://localhost:3000")).rstrip("/") + "/api/annotations"
    token = token or os.getenv("GRAFANA_TOKEN")
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})}
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=5).read())


async def check_pipeline(pipeline_id: str, *,
                         fetch_scores: Callable[[str], Awaitable[list[float]]],
                         fetch_suggestions: Callable[[str], Awaitable[list[dict]]],
                         post_annotation: Callable[[dict], Awaitable[None]] = post_grafana_annotation) -> dict | None:
    """fetch_scores(pipeline_id) -> overall scores of the last 7 days, oldest first.
    fetch_suggestions(pipeline_id) -> the Task 38 optimizer's suggestions (backend.services.pipeline_optimizer)."""
    a = detect_anomaly(pipeline_id, await fetch_scores(pipeline_id))
    if a is None:
        return None
    suggestions = await fetch_suggestions(pipeline_id)
    payload = annotation_payload(a, suggestions)
    await post_annotation(payload)
    return {"anomaly": asdict(a), "suggestions": suggestions, "annotation": payload}
