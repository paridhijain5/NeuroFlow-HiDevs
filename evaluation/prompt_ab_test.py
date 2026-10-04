#!/usr/bin/env python3
"""A/B test two system-prompt variants; both runs are logged to the MLflow experiment
'prompt_ab_test' so results are visible side by side. Prints the winner.

  python evaluation/prompt_ab_test.py --api-url https://<app> --variants A,B
Your API must honour the `prompt_variant` request field (see _ask() in generation_eval.py).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from generation_eval import run_eval


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--token", default=os.getenv("API_TOKEN", ""))
    p.add_argument("--questions", default="evaluation/golden_set.json")
    p.add_argument("--variants", default="A,B")
    a = p.parse_args()
    questions = json.loads(Path(a.questions).read_text())
    results = {
        v: run_eval(questions, a.api_url, a.token, v, experiment="prompt_ab_test", run_name=f"variant_{v}")
        for v in a.variants.split(",")
    }
    for v, r in results.items():
        print(f"variant {v}: overall={r['overall']:.3f} faithfulness={r['faithfulness']:.3f} "
              f"relevance={r['answer_relevance']:.3f} p95={r['p95_latency_s']:.2f}s")
    winner = max(results, key=lambda v: results[v]["overall"])
    print(f"Winner: variant {winner}  ->  set PROMPT_VARIANT={winner}")
    Path("evaluation/prompt_ab_results.json").write_text(
        json.dumps({v: {k: x for k, x in r.items() if k != "per_question"} for v, r in results.items()}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
