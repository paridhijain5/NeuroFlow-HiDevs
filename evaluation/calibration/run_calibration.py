"""Pearson correlation between automated faithfulness and human scores.

  python -m evaluation.calibration.run_calibration            # real LLM (needs evaluation/bootstrap.py wired)
  python -m evaluation.calibration.run_calibration --mock     # offline heuristic judge (demo only)
"""
import argparse, asyncio, json
from pathlib import Path

from scipy.stats import pearsonr

from ..metrics import evaluate_faithfulness

HERE = Path(__file__).parent
THRESHOLD = 0.85


async def run(llm, data: list[dict]) -> list[float]:
    return await asyncio.gather(*(
        evaluate_faithfulness(d["query"], d["answer"], d["context"], llm=llm) for d in data))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--data", default=str(HERE / "annotated_set.json"))
    ap.add_argument("--out", default=str(HERE.parent / "calibration_results.json"))
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text())
    if args.mock:
        from ..mock_llm import MockLLM
        llm = MockLLM()
    else:
        from ..bootstrap import build_llm_and_embedder
        llm, _ = build_llm_and_embedder()

    auto = asyncio.run(run(llm, data))
    human = [d["human_score"] for d in data]
    r, _ = pearsonr(auto, human)
    result = {"pearson_correlation": round(float(r), 4), "n_examples": len(data),
              "threshold": THRESHOLD, "passed": bool(r > THRESHOLD),
              "mode": "mock-heuristic" if args.mock else "llm"}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
