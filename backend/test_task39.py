"""Offline self-test for the Task 39 checklist (real MLflow file store, mock provider/Redis).
Run from the repo root:  python -m backend.test_task39
"""
import asyncio, json, os, tempfile, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp}/mlflow.db"

import mlflow  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from mlflow.tracking import MlflowClient  # noqa: E402

from backend.task39_demo_app import GOOD_ANSWER, FakeRedis, MockFineTuneProvider, create_app  # noqa: E402
from pipelines.finetuning.extractor import contains_pii, extract_dpo_pairs  # noqa: E402
from pipelines.finetuning.repo import InMemoryFineTuneRepo  # noqa: E402

os.chdir(tmp)   # sqlite store keeps artifacts in ./mlruns -> keep them inside tmp
NOW = datetime.now(timezone.utc)
CTX = "[Source 1] Liability is capped at fees paid."


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    assert cond, name


def row(query, answer=GOOD_ANSWER, q=0.9, faith=0.9, rating=None, included=None, i=0):
    return dict(run_id=str(uuid.uuid4()), query=query, context=CTX, answer=answer, quality_score=q,
                faithfulness=faith, user_rating=rating, included_in_job=included,
                created_at=NOW - timedelta(days=i))


reeval_calls = []


async def reeval(pair):                       # stands in for the Task 37 faithfulness metric
    reeval_calls.append(pair.run_id)
    return 0.9 if "SUPPORTED" in pair.answer else 0.3


def build_repo():
    r = InMemoryFineTuneRepo()
    for i in range(12):                                                   # 12 clean pairs
        r.add_row(**row(f"What does clause {i} say?", rating=[5, 4, None][i % 3], i=i))
    r.add_row(**row("faith missing but supported", answer=GOOD_ANSWER + " SUPPORTED", faith=None))  # valid after re-eval
    # --- never extracted
    r.add_row(**row("low quality score", q=0.70))
    r.add_row(**row("already used", included=str(uuid.uuid4())))
    r.add_row(**row("rejected response", rating=2))
    r.add_row(**row("rating one", rating=1))
    # --- extracted but fail validation
    r.add_row(**row("too short", answer="Yes [Source 1]."))
    r.add_row(**row("no citation", answer=GOOD_ANSWER.replace("[Source", "(Source")))
    r.add_row(**row("email me at jane.doe@example.com about this"))
    r.add_row(**row("call +1 (415) 555-0132 please"))
    r.add_row(**row("low faithfulness", faith=0.7))
    r.add_row(**row("faith missing + unsupported", faith=None))
    r.add_row(**row("too long", answer="[Source 1] " + "word " * 2100))
    return r


def main():
    repo, provider, redis = build_repo(), MockFineTuneProvider(), FakeRedis()
    data_dir = f"{tmp}/training_data"
    app = create_app(repo, provider, redis, data_dir, faithfulness_fn=reeval, poll_seconds=None, do_seed=False)
    c = TestClient(app)
    mgr = app.state.finetune_manager

    # ---- PII helper
    check("PII regex: email + phone caught, normal numbers ignored",
          contains_pii("a@b.co") and contains_pii("+91 98765 43210") and contains_pii("415-555-0132")
          and not contains_pii("clause 12 on page 3 of 2024") and not contains_pii("order 12345"))

    # ---- preview: no side effects
    before = [dict(r) for r in repo.rows]
    p = c.get("/finetune/training-data/preview").json()
    check("preview returns 5 samples", len(p["samples"]) == 5 and p["samples"][0]["messages"][2]["role"] == "assistant", {k: p[k] for k in ("candidates", "would_pass_validation", "would_reject")})
    check("preview filters by quality/rating/used (rows excluded before validation)",
          p["candidates"] == 12 + 1 + 7)
    check("preview has no side effects (no job, no files, nothing marked, no LLM re-eval)",
          not repo.jobs and not Path(data_dir).exists() and not reeval_calls
          and [r["included_in_job"] for r in repo.rows] == [r["included_in_job"] for r in before])

    # ---- create job
    r = c.post("/finetune/jobs", json={"base_model": "gpt-4o-mini-2024-07-18", "domain": "legal"})
    check("POST /finetune/jobs -> 201 + job_id", r.status_code == 201, r.json())
    job_id, stats = r.json()["job_id"], r.json()
    check("validation: 13 valid, rest rejected with reasons", stats["valid"] == 13 and stats["extracted"] == 20,
          stats["rejected_reasons"])
    reasons = stats["rejected_reasons"]
    check("rejects short, uncited, PII, low-faithfulness, too-long",
          all(k in reasons for k in ("assistant_too_short", "no_citation", "pii_in_query", "low_faithfulness",
                                     "assistant_too_long")) and reasons["pii_in_query"] == 2)
    check("missing faithfulness is re-evaluated (2 calls)", len(reeval_calls) == 2)

    lines = Path(f"{data_dir}/{job_id}.jsonl").read_text().strip().splitlines()
    recs = [json.loads(l) for l in lines]
    check("JSONL written: 13 lines, OpenAI messages format (system/user/assistant)",
          len(recs) == 13 and all([m["role"] for m in x["messages"]] == ["system", "user", "assistant"] for x in recs))
    check("user message uses [Context]/[Question] layout and no PII leaked",
          all(x["messages"][1]["content"].startswith("[Context]\n") and "\n[Question]\n" in x["messages"][1]["content"]
              and not contains_pii(x["messages"][1]["content"].split("[Question]")[1]) for x in recs))
    check("the 13 used pairs are marked included_in_job; others untouched",
          sum(1 for r in repo.rows if r["included_in_job"] == job_id) == 13)

    # ---- MLflow run
    job = c.get(f"/finetune/jobs/{job_id}").json()
    ml = MlflowClient(); run = ml.get_run(job["mlflow_run_id"])
    params = run.data.params
    check("MLflow run has base_model / training_pair_count / avg_quality_score / date_range",
          params["base_model"] == "gpt-4o-mini-2024-07-18" and params["training_pair_count"] == "13"
          and 0.8 < float(params["avg_quality_score"]) < 1 and " to " in params["date_range"], params)
    check("training data logged as MLflow artifact",
          any(a.path == f"{job_id}.jsonl" for a in ml.list_artifacts(job["mlflow_run_id"])))
    check("job status submitted + MLflow URL exposed",
          job["status"] == "submitted" and "/#/experiments/" in job["mlflow_run_url"] and job["provider_job_id"])

    # ---- second job: pairs already consumed
    r2 = c.post("/finetune/jobs", json={"domain": "legal"})
    check("pairs are not reused: second job -> 422 insufficient data", r2.status_code == 422, r2.json()["detail"]["valid"])

    # ---- poll: running -> succeeded -> registered
    asyncio.run(mgr.poll_once())
    check("poll #1 -> running", c.get(f"/finetune/jobs/{job_id}").json()["status"] == "running")
    check("model not registered while running", not redis.hashes.get("router:models"))
    asyncio.run(mgr.poll_once())
    job = c.get(f"/finetune/jobs/{job_id}").json()
    check("poll #2 -> succeeded, fine-tuned model name recorded", job["status"] == "succeeded" and job["fine_tuned_model"].startswith("ft:"), job["fine_tuned_model"])
    reg = json.loads(asyncio.run(redis.hget("router:models", job["fine_tuned_model"])))
    check("model registered in router:models with task_type = domain", reg["task_type"] == "legal" and reg["fine_tuned"])
    run = ml.get_run(job["mlflow_run_id"])
    check("MLflow metrics logged after completion",
          run.data.metrics["training_loss"] == 0.41 and run.data.metrics["validation_loss"] == 0.52
          and run.data.metrics["training_token_count"] == 48210, run.data.metrics)
    mvs = ml.search_model_versions(f"name='neuroflow-finetune-{job_id}'")
    check("mlflow.register_model -> neuroflow-finetune-{job_id}", len(mvs) == 1, [m.name for m in mvs])
    check("job detail exposes training metrics", job["metrics"]["training_loss"] == 0.41 and job["metrics"]["mlflow_model_version"] == "1")
    n_active = asyncio.run(mgr.poll_once())
    check("finished jobs are not polled again (no duplicate registration)", n_active == 0)
    check("GET /finetune/jobs lists the job", job_id in [j["id"] for j in c.get("/finetune/jobs").json()["jobs"]])
    check("unknown job -> 404", c.get("/finetune/jobs/nope").status_code == 404)

    # ---- failure path keeps pairs available
    class BoomProvider(MockFineTuneProvider):
        async def submit(self, *a): raise RuntimeError("provider down")
    repo2 = build_repo()
    app2 = create_app(repo2, BoomProvider(), FakeRedis(), f"{tmp}/td2", faithfulness_fn=reeval, poll_seconds=None, do_seed=False)
    try:
        TestClient(app2, raise_server_exceptions=True).post("/finetune/jobs", json={})
        check("provider failure surfaces", False)
    except RuntimeError:
        check("provider failure: job failed, pairs NOT consumed",
              all(r["included_in_job"] is None or r["query"] == "already used" for r in repo2.rows)
              and next(iter(repo2.jobs.values()))["status"] == "failed")

    # ---- too few pairs
    repo3 = InMemoryFineTuneRepo()
    for i in range(4): repo3.add_row(**row(f"q{i}"))
    c3 = TestClient(create_app(repo3, MockFineTuneProvider(), FakeRedis(), f"{tmp}/td3", poll_seconds=None, do_seed=False))
    check("fewer than 10 valid pairs -> 422", c3.post("/finetune/jobs", json={}).status_code == 422)

    # ---- DPO
    rated = [row("Q1 liability?", answer="great answer [Source 1]", rating=5), row("q1 liability?", answer="bad answer", rating=1),
             row("Q2 term?", answer="fine", rating=5), row("Q3 pay?", answer="good", rating=4),
             row("Q3 pay?", answer="poor", rating=2), row("Q4 meh?", answer="a", rating=3), row("Q4 meh?", answer="b", rating=3)]
    pairs = extract_dpo_pairs(rated)
    check("DPO: pairs only when good(>=4) AND bad(<=2) exist for same query",
          len(pairs) == 2 and set(pairs[0]) == {"prompt", "chosen", "rejected"}, pairs)
    repo4 = InMemoryFineTuneRepo()
    for r_ in rated: repo4.add_row(**r_)
    c4 = TestClient(create_app(repo4, MockFineTuneProvider(), FakeRedis(), f"{tmp}/td4", poll_seconds=None, do_seed=False))
    check("GET /finetune/dpo-data/preview", c4.get("/finetune/dpo-data/preview").json()["total_pairs"] == 2)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
