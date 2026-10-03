"""Training-data extraction + validation (SFT JSONL, plus optional DPO preference pairs)."""
from __future__ import annotations

import json, re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Iterable

QUALITY_THRESHOLD = 0.82          # evaluation threshold from Task 37/38
MIN_RATING = 4                    # user_rating >= 4 OR NULL
MIN_ANSWER_TOKENS, MAX_ANSWER_TOKENS = 50, 2000
MIN_FAITHFULNESS = 0.8            # must be strictly greater
DEFAULT_SYSTEM = ("You are a precise research assistant. Answer the user's question using ONLY the provided "
                  "context.")

CITATION_RE = re.compile(r"\[Source \d+\]")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_CANDIDATE_RE = re.compile(r"\+?\d[\d\s().\-]{8,}\d")

FaithfulnessFn = Callable[["TrainingPair"], Awaitable[float]]


def count_tokens(text: str) -> int:
    """Cheap estimate (~4 chars/token). Swap in tiktoken if you need exact counts."""
    return max(1, round(len(text) / 4))


def contains_pii(text: str) -> bool:
    if EMAIL_RE.search(text):
        return True
    return any(sum(ch.isdigit() for ch in m.group(0)) >= 10 for m in PHONE_CANDIDATE_RE.finditer(text))


@dataclass
class TrainingPair:
    run_id: str
    query: str
    context: str                      # formatted "[Source N] ..." chunks
    answer: str
    quality_score: float
    created_at: datetime
    system_prompt: str = DEFAULT_SYSTEM
    faithfulness: float | None = None
    user_rating: int | None = None

    def messages(self) -> list[dict]:
        return [{"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"[Context]\n{self.context}\n[Question]\n{self.query}"},
                {"role": "assistant", "content": self.answer}]

    def to_jsonl_line(self) -> str:
        return json.dumps({"messages": self.messages()}, ensure_ascii=False)


async def validate_pair(pair: TrainingPair, faithfulness_fn: FaithfulnessFn | None = None,
                        allow_pending_faithfulness: bool = False) -> list[str]:
    """Returns the list of rejection reasons (empty = valid). Re-evaluates faithfulness if missing."""
    reasons: list[str] = []
    n = count_tokens(pair.answer)
    if n < MIN_ANSWER_TOKENS:
        reasons.append("assistant_too_short")
    if n > MAX_ANSWER_TOKENS:
        reasons.append("assistant_too_long")
    if not CITATION_RE.search(pair.answer):
        reasons.append("no_citation")
    if contains_pii(pair.query):
        reasons.append("pii_in_query")

    if pair.faithfulness is None and faithfulness_fn is not None:
        try:
            pair.faithfulness = await faithfulness_fn(pair)
        except Exception:
            reasons.append("faithfulness_unavailable")
    if pair.faithfulness is None:
        if not allow_pending_faithfulness and "faithfulness_unavailable" not in reasons:
            reasons.append("faithfulness_unavailable")
    elif not pair.faithfulness > MIN_FAITHFULNESS:
        reasons.append("low_faithfulness")
    return reasons


@dataclass
class DatasetResult:
    job_id: str
    path: str | None
    pairs: list[TrainingPair]
    extracted: int
    rejected: dict[str, int] = field(default_factory=dict)
    rejected_examples: list[dict] = field(default_factory=list)

    @property
    def valid(self) -> int:
        return len(self.pairs)


async def build_dataset(repo, job_id: str, out_dir: str | Path = "training_data", *,
                        min_quality: float = QUALITY_THRESHOLD,
                        faithfulness_fn: FaithfulnessFn | None = None,
                        limit: int | None = None) -> DatasetResult:
    """Extract -> validate -> write training_data/{job_id}.jsonl. Does NOT mark pairs as used."""
    candidates = await repo.fetch_candidate_pairs(min_quality, limit)
    valid: list[TrainingPair] = []
    reasons_count: Counter = Counter()
    examples: list[dict] = []
    for p in candidates:
        reasons = await validate_pair(p, faithfulness_fn)
        if reasons:
            reasons_count.update(reasons)
            if len(examples) < 10:
                examples.append({"run_id": p.run_id, "reasons": reasons})
        else:
            valid.append(p)

    path = None
    if valid:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = str(out / f"{job_id}.jsonl")
        Path(path).write_text("\n".join(p.to_jsonl_line() for p in valid) + "\n", encoding="utf-8")
    return DatasetResult(str(job_id), path, valid, len(candidates), dict(reasons_count), examples)


async def preview_training_data(repo, n: int = 5, min_quality: float = QUALITY_THRESHOLD) -> dict:
    """What a job would extract right now. Pure read: no files, no marking, no LLM re-evaluation."""
    candidates = await repo.fetch_candidate_pairs(min_quality, None)
    samples, would_pass, rejected, pending = [], 0, Counter(), 0
    for p in candidates:
        reasons = await validate_pair(p, None, allow_pending_faithfulness=True)
        if reasons:
            rejected.update(reasons)
            continue
        would_pass += 1
        needs_reeval = p.faithfulness is None
        pending += needs_reeval
        if len(samples) < n:
            samples.append({"run_id": p.run_id, "quality_score": p.quality_score, "faithfulness": p.faithfulness,
                            "user_rating": p.user_rating, "needs_faithfulness_reevaluation": needs_reeval,
                            "messages": p.messages()})
    return {"candidates": len(candidates), "would_pass_validation": would_pass,
            "would_reject": len(candidates) - would_pass, "rejection_reasons": dict(rejected),
            "pending_reevaluation": pending, "samples": samples}


# ------------------------------------------------------------------ DPO ("go further")
def extract_dpo_pairs(rated_rows: Iterable[dict]) -> list[dict]:
    """For each query with a good (rating>=4) AND a bad (rating<=2) response:
       {"prompt": query, "chosen": good_response, "rejected": bad_response}.
    rated_rows: dicts with query, answer, user_rating (and optional quality_score)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rated_rows:
        if r.get("user_rating") is not None and r.get("answer") and not contains_pii(r["query"]):
            groups[r["query"].strip().lower()].append(r)
    out = []
    for rows in groups.values():
        good = [r for r in rows if r["user_rating"] >= 4]
        bad = [r for r in rows if r["user_rating"] <= 2]
        if not good or not bad:
            continue
        chosen = max(good, key=lambda r: (r["user_rating"], r.get("quality_score") or 0))
        rejected = min(bad, key=lambda r: (r["user_rating"], r.get("quality_score") or 0))
        if chosen["answer"] != rejected["answer"]:
            out.append({"prompt": chosen["query"], "chosen": chosen["answer"], "rejected": rejected["answer"]})
    return out


def write_dpo_jsonl(pairs: list[dict], path: str | Path) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n", encoding="utf-8")
    return str(path)
