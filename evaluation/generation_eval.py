#!/usr/bin/env python3
"""Generation-quality evaluation on the 30-question golden set (LLM-as-judge).

Measures: faithfulness, answer_relevance, context_precision, overall (mean of the three),
and p95 query latency. Writes evaluation/generation_results.json and logs to MLflow.

Usage:
  python evaluation/generation_eval.py --api-url https://<app> [--token JWT] \
      [--questions evaluation/golden_set.json] [--variant A] [--experiment quality_sprint]

ADAPT: response field names in _extract() and the request payload in _ask() to match your API.
If you already have a judge from your Evaluation task, reuse it inside _judge().
Needs: pip install requests openai mlflow  and OPENAI_API_KEY in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

import requests

JUDGE_PROMPT = """You are a strict evaluator of a RAG system. Score each from 0.0 to 1.0.
- faithfulness: every claim in the answer is supported by the contexts (no hallucination)
- answer_relevance: the answer directly and completely addresses the question
- context_precision: the retrieved contexts are relevant to the question (low noise)
Return ONLY JSON: {{"faithfulness": x, "answer_relevance": y, "context_precision": z}}

QUESTION: {question}
CONTEXTS: {contexts}
ANSWER: {answer}"""


def _ask(api_url: str, token: str, question: str, variant: str) -> tuple[dict[str, Any], float]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    payload = {"query": question, "prompt_variant": variant}  # ADAPT
    start = time.perf_counter()
    r = requests.post(f"{api_url.rstrip('/')}/query", json=payload, headers=headers, timeout=180)
    latency = time.perf_counter() - start
    r.raise_for_status()
    return r.json(), latency


def _extract(body: dict[str, Any]) -> tuple[str, list[str]]:
    answer = body.get("answer") or body.get("response") or ""
    ctx = body.get("contexts")
    if ctx is None:
        ctx = [s.get("text", "") if isinstance(s, dict) else str(s) for s in body.get("sources", [])]
    return str(answer), [str(c) for c in ctx]


def _judge(question: str, answer: str, contexts: list[str], model: str) -> dict[str, float]:
    from openai import OpenAI

    client = OpenAI()
    prompt = JUDGE_PROMPT.format(
        question=question, contexts="\n---\n".join(contexts)[:12000], answer=answer
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    scores = json.loads(resp.choices[0].message.content or "{}")
    return {k: float(scores.get(k, 0.0)) for k in ("faithfulness", "answer_relevance", "context_precision")}


def _p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def run_eval(
    questions: list[dict[str, Any]],
    api_url: str,
    token: str = "",
    variant: str = "A",
    judge_model: str = "gpt-4o-mini",
    experiment: str = "quality_sprint",
    run_name: str | None = None,
) -> dict[str, Any]:
    per_q: list[dict[str, Any]] = []
    latencies: list[float] = []
    for i, q in enumerate(questions, 1):
        text = q["question"] if isinstance(q, dict) else str(q)
        body, latency = _ask(api_url, token, text, variant)
        answer, contexts = _extract(body)
        scores = _judge(text, answer, contexts, judge_model)
        latencies.append(latency)
        per_q.append({"question": text, "latency_s": latency, **scores})
        print(f"[{i}/{len(questions)}] {latency:.2f}s {scores}")
    avg = {
        k: statistics.mean(r[k] for r in per_q)
        for k in ("faithfulness", "answer_relevance", "context_precision")
    }
    result = {
        "variant": variant,
        "n_questions": len(per_q),
        **avg,
        "overall": statistics.mean(avg.values()),
        "p95_latency_s": _p95(latencies),
        "per_question": per_q,
    }
    try:
        import mlflow

        if os.getenv("MLFLOW_TRACKING_URI"):
            mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(experiment)
        with mlflow.start_run(run_name=run_name or f"generation_eval_variant_{variant}"):
            mlflow.log_params({"prompt_variant": variant, "judge_model": judge_model})
            mlflow.log_metrics({k: v for k, v in result.items() if isinstance(v, float)})
    except Exception as exc:  # MLflow is optional for local runs
        print(f"MLflow logging skipped: {exc}")
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--token", default=os.getenv("API_TOKEN", ""))
    p.add_argument("--questions", default="evaluation/golden_set.json")
    p.add_argument("--variant", default="A")
    p.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", "gpt-4o-mini"))
    p.add_argument("--experiment", default="quality_sprint")
    p.add_argument("--out", default="evaluation/generation_results.json")
    a = p.parse_args()
    questions = json.loads(Path(a.questions).read_text())
    result = run_eval(questions, a.api_url, a.token, a.variant, a.judge_model, a.experiment)
    Path(a.out).write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "per_question"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
