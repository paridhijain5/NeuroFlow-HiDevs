"""Runnable demo (seeded in-memory pairs, mock provider, fake Redis, REAL MLflow file store).

    mlflow server --port 5000 &                              # optional UI at http://localhost:5000
    export MLFLOW_TRACKING_URI=http://localhost:5000         # or leave unset -> local sqlite:///mlflow.db
    uvicorn backend.task39_demo_app:app --port 8000          # from the repo root
"""
import asyncio, os, uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

from backend.api import finetune
from pipelines.finetuning.job_manager import FineTuneJobManager, ProviderStatus
from pipelines.finetuning.repo import InMemoryFineTuneRepo

if "MLFLOW_TRACKING_URI" not in os.environ:
    os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///mlflow.db"

GOOD_ANSWER = ("Based on the provided sources, the agreement limits liability to the fees paid in the prior twelve "
               "months [Source 1]. Indirect and consequential damages are excluded, except for breaches of "
               "confidentiality or wilful misconduct [Source 2]. These caps apply to both parties equally "
               "and survive termination of the contract [Source 3].")


class FakeRedis:
    def __init__(self): self.hashes = {}
    async def hset(self, key, field, value): self.hashes.setdefault(key, {})[field] = value
    async def hget(self, key, field): return self.hashes.get(key, {}).get(field)


class MockFineTuneProvider:
    """submit -> ftjob id; 1st poll 'running'; 2nd poll 'succeeded' (with losses + tokens)."""

    def __init__(self): self.polls: dict[str, int] = {}; self.n = 0; self.submitted = []

    async def submit(self, jsonl_path, base_model):
        self.n += 1; pid = f"ftjob-mock-{self.n}"; self.submitted.append((pid, jsonl_path, base_model)); return pid

    async def get_status(self, pid):
        self.polls[pid] = self.polls.get(pid, 0) + 1
        if self.polls[pid] < 2:
            return ProviderStatus("running")
        return ProviderStatus("succeeded", f"ft:gpt-4o-mini:neuroflow::{pid[-6:]}", 48210, 0.41, 0.52)


def seed(repo: InMemoryFineTuneRepo, n_good: int = 12) -> None:
    now = datetime.now(timezone.utc)
    ctx = "[Source 1] Liability is capped at fees paid.\n\n[Source 2] Consequential damages excluded."
    for i in range(n_good):
        repo.add_row(run_id=str(uuid.uuid4()), query=f"What does clause {i + 1} say about liability?",
                     context=ctx, answer=GOOD_ANSWER, quality_score=0.84 + (i % 5) / 100,
                     faithfulness=0.9, user_rating=[5, 4, None][i % 3], created_at=now - timedelta(days=i))


def create_app(repo=None, provider=None, redis=None, data_dir="training_data", faithfulness_fn=None,
               poll_seconds: float | None = 2.0, do_seed: bool = True) -> FastAPI:
    repo = repo or InMemoryFineTuneRepo()
    if do_seed and repo is not None and not repo.rows:
        seed(repo)
    provider, redis = provider or MockFineTuneProvider(), redis or FakeRedis()
    manager = FineTuneJobManager(repo, provider, redis, data_dir=data_dir, faithfulness_fn=faithfulness_fn)
    app = FastAPI(title="NeuroFlow fine-tuning (demo)")
    app.state.finetune_repo, app.state.finetune_manager = repo, manager
    app.state.redis, app.state.provider = redis, provider
    app.include_router(finetune.router)

    if poll_seconds:
        @app.on_event("startup")
        async def _poller():     # stands in for the arq cron (every 60s in production)
            async def loop():
                while True:
                    await asyncio.sleep(poll_seconds)
                    await manager.poll_once()
            app.state.poll_task = asyncio.create_task(loop())
    return app


app = create_app()
