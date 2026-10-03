"""Rule-based config suggestions from evaluation results ("Go further")."""
from __future__ import annotations

MIN_EVALUATED_RUNS = 5   # "consistently low" needs a minimum sample


def _s(rule, metric, observed, threshold, path, current, suggested, rationale):
    return {"rule": rule, "metric": metric, "observed": observed, "threshold": threshold,
            "path": path, "current": current, "suggested": suggested, "rationale": rationale}


def suggest(config: dict, analytics: dict) -> list[dict]:
    scores = analytics["avg_eval_scores"]
    if scores["evaluated_runs"] < MIN_EVALUATED_RUNS:
        return []
    out: list[dict] = []
    ret, gen = config["retrieval"], config["generation"]

    cp = scores.get("context_precision")
    if cp is not None and cp < 0.6 and ret["top_k_after_rerank"] > 3:
        new = max(3, ret["top_k_after_rerank"] - 2)
        out.append(_s("low_context_precision", "context_precision", cp, 0.6,
                      "retrieval.top_k_after_rerank", ret["top_k_after_rerank"], new,
                      "Many retrieved chunks are not used; keeping fewer after reranking cuts noise."))
    cr = scores.get("context_recall")
    if cr is not None and cr < 0.6 and ret["dense_k"] < 100:
        new = min(100, ret["dense_k"] + 10)
        out.append(_s("low_context_recall", "context_recall", cr, 0.6, "retrieval.dense_k",
                      ret["dense_k"], new, "Relevant sources are being missed; widen the dense candidate pool."))
    f = scores.get("faithfulness")
    if f is not None and f < 0.7:
        if gen["temperature"] > 0.1:
            out.append(_s("low_faithfulness", "faithfulness", f, 0.7, "generation.temperature",
                          gen["temperature"], 0.1, "Answers contain ungrounded claims; lower the sampling temperature."))
        elif gen["system_prompt_variant"] != "precise":
            out.append(_s("low_faithfulness", "faithfulness", f, 0.7, "generation.system_prompt_variant",
                          gen["system_prompt_variant"], "precise", "Use the stricter 'precise' prompt variant."))
    ar = scores.get("answer_relevance")
    if ar is not None and ar < 0.6 and not ret["query_expansion"]:
        out.append(_s("low_answer_relevance", "answer_relevance", ar, 0.6, "retrieval.query_expansion",
                      False, True, "Answers drift from the question; query expansion can improve retrieval."))
    lat = analytics["retrieval_latency_ms"]["p95"]
    gen_avg = analytics["generation_latency_ms"]["avg"]
    if lat is not None and gen_avg is not None and lat + gen_avg > 5000 and gen["max_context_tokens"] > 1500:
        new = max(1000, int(gen["max_context_tokens"] * 0.75))
        out.append(_s("high_latency", "latency", round(lat + gen_avg, 1), 5000, "generation.max_context_tokens",
                      gen["max_context_tokens"], new, "p95 retrieval + avg generation latency is high; trim context."))
    return out
