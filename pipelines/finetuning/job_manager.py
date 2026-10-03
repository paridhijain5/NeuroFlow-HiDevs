"""Fine-tuning job lifecycle: extract -> validate -> MLflow run -> submit -> poll -> register."""
from __future__ import annotations

import asyncio, json, logging, uuid
from dataclasses import dataclass
from typing import Any, Protocol

from . import tracker
from .extractor import QUALITY_THRESHOLD, FaithfulnessFn, build_dataset

log = logging.getLogger("neuroflow.finetune")

MIN_TRAINING_PAIRS = 10          # OpenAI requires >= 10 examples
POLL_STATUSES = ("submitted", "running")
_STATUS_MAP = {"validating_files": "submitted", "queued": "submitted", "running": "running",
               "succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled"}


class InsufficientDataError(Exception):
    def __init__(self, stats: dict):
        super().__init__(f"need at least {MIN_TRAINING_PAIRS} valid training pairs, got {stats['valid']}")
        self.stats = stats


@dataclass
class ProviderStatus:
    status: str
    fine_tuned_model: str | None = None
    trained_tokens: int | None = None
    training_loss: float | None = None
    validation_loss: float | None = None
    error: str | None = None


class FineTuneProvider(Protocol):
    async def submit(self, jsonl_path: str, base_model: str) -> str: ...
    async def get_status(self, provider_job_id: str) -> ProviderStatus: ...


class OpenAIFineTuneProvider:
    """NOTE: not exercised by the offline tests (needs OPENAI_API_KEY)."""

    def __init__(self, client: Any = None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI()
        return self._client

    async def submit(self, jsonl_path: str, base_model: str) -> str:
        with open(jsonl_path, "rb") as f:
            file_resp = await self.client.files.create(file=f, purpose="fine-tune")
        job = await self.client.fine_tuning.jobs.create(training_file=file_resp.id, model=base_model)
        return job.id

    async def get_status(self, provider_job_id: str) -> ProviderStatus:
        job = await self.client.fine_tuning.jobs.retrieve(provider_job_id)
        train_loss = valid_loss = None
        try:   # losses live in "metrics" events (newest first); best-effort
            events = await self.client.fine_tuning.jobs.list_events(provider_job_id, limit=50)
            for ev in events.data:
                if getattr(ev, "type", None) == "metrics":
                    d = ev.data or {}
                    train_loss, valid_loss = d.get("train_loss"), d.get("valid_loss")
                    break
        except Exception:
            pass
        err = getattr(job, "error", None)
        return ProviderStatus(job.status, job.fine_tuned_model, job.trained_tokens, train_loss, valid_loss,
                              getattr(err, "message", None) if err else None)


async def register_in_router(redis, model_name: str, task_type: str, base_model: str, job_id: str) -> None:
    """Add the fine-tuned model to the Redis `router:models` registry with task_type = trained domain.
    ADAPT: match the structure your ModelRouter reads (assumed: hash of model name -> JSON)."""
    await redis.hset("router:models", model_name, json.dumps({
        "provider": "openai", "task_type": task_type, "fine_tuned": True,
        "base_model": base_model, "finetune_job_id": str(job_id)}))


class FineTuneJobManager:
    def __init__(self, repo, provider: FineTuneProvider, redis, *, data_dir: str = "training_data",
                 faithfulness_fn: FaithfulnessFn | None = None, min_pairs: int = MIN_TRAINING_PAIRS):
        self.repo, self.provider, self.redis = repo, provider, redis
        self.data_dir, self.faithfulness_fn, self.min_pairs = data_dir, faithfulness_fn, min_pairs

    async def create_job(self, base_model: str, domain: str = "rag_generation",
                         min_quality: float = QUALITY_THRESHOLD) -> dict:
        job_id = str(uuid.uuid4())
        await self.repo.create_job(job_id, base_model, domain)
        ds = await build_dataset(self.repo, job_id, self.data_dir, min_quality=min_quality,
                                 faithfulness_fn=self.faithfulness_fn)
        stats = {"extracted": ds.extracted, "valid": ds.valid, "rejected_reasons": ds.rejected}
        if ds.valid < self.min_pairs:
            await self.repo.update_job(job_id, status="failed", error=f"insufficient data: {stats}")
            raise InsufficientDataError(stats)

        run_id = None
        try:
            run_id = await asyncio.to_thread(tracker.start_training_job, job_id, ds.pairs, base_model, ds.path)
            run_url = await asyncio.to_thread(tracker.run_url, run_id)
            provider_job_id = await self.provider.submit(ds.path, base_model)
        except Exception as exc:
            log.exception("job %s failed to start", job_id)
            if run_id:
                await asyncio.to_thread(tracker.mark_run_failed, run_id, str(exc))
            await self.repo.update_job(job_id, status="failed", error=str(exc), mlflow_run_id=run_id)
            raise
        # only consume the pairs once the provider accepted the job
        await self.repo.mark_included([p.run_id for p in ds.pairs], job_id)
        await self.repo.update_job(job_id, status="submitted", provider_job_id=provider_job_id,
                                   mlflow_run_id=run_id, mlflow_run_url=run_url,
                                   training_pair_count=ds.valid, dataset_path=ds.path)
        return {"job_id": job_id, **stats}

    async def poll_once(self) -> int:
        """One polling pass (the arq task calls this every 60s). Returns jobs checked."""
        jobs = await self.repo.list_active_jobs()
        for job in jobs:
            try:
                st = await self.provider.get_status(job["provider_job_id"])
                await self._apply(job, st)
            except Exception:
                log.exception("polling job %s failed", job["id"])
        return len(jobs)

    async def _apply(self, job: dict, st: ProviderStatus) -> None:
        status = _STATUS_MAP.get(st.status, "running")
        if status == "succeeded":
            await self._on_success(job, st)
        elif status in ("failed", "cancelled"):
            await self.repo.update_job(job["id"], status=status, error=st.error)
            if job.get("mlflow_run_id"):
                await asyncio.to_thread(tracker.mark_run_failed, job["mlflow_run_id"], st.error or status)
        elif status != job["status"]:
            await self.repo.update_job(job["id"], status=status)

    async def _on_success(self, job: dict, st: ProviderStatus) -> None:
        """Side effects first, status flip LAST: if anything below fails the job stays 'running' and the next
        poll retries (router + metrics writes are idempotent)."""
        jid = job["id"]
        metrics = {k: v for k, v in {"training_loss": st.training_loss, "validation_loss": st.validation_loss,
                                     "training_token_count": st.trained_tokens}.items() if v is not None}
        # 1. router registry (task_type = domain) - the part users actually depend on
        await register_in_router(self.redis, st.fine_tuned_model, job["domain"], job["base_model"], jid)
        await self.repo.update_job(jid, fine_tuned_model=st.fine_tuned_model)
        # 2. MLflow: metrics on the original run + model registry entry
        run_id = job["mlflow_run_id"]
        await asyncio.to_thread(tracker.log_job_metrics, run_id, training_loss=st.training_loss,
                                validation_loss=st.validation_loss, trained_tokens=st.trained_tokens)
        version = await asyncio.to_thread(tracker.register_model, run_id, jid, {
            "provider_job_id": job["provider_job_id"], "fine_tuned_model": st.fine_tuned_model,
            "base_model": job["base_model"], "domain": job["domain"]})
        # 3. done
        await self.repo.update_job(jid, status="succeeded", metrics={**metrics, "mlflow_model_version": version})
        log.info("job %s succeeded -> %s (registered v%s)", jid, st.fine_tuned_model, version)


# ---- arq scheduled task: poll every 60 seconds ------------------------------------------------
async def poll_finetune_jobs(ctx: dict) -> int:
    return await ctx["manager"].poll_once()


def build_worker_settings(manager: FineTuneJobManager, redis_settings=None):
    """Usage (module-level in your worker file):  WorkerSettings = build_worker_settings(manager, RedisSettings())
    run with:  arq your_worker.WorkerSettings"""
    from arq import cron

    async def startup(ctx):
        ctx["manager"] = manager

    class WorkerSettings:
        functions = [poll_finetune_jobs]
        cron_jobs = [cron(poll_finetune_jobs, second=0)]      # second=0 -> every minute (60s)
        on_startup = startup

    if redis_settings is not None:
        WorkerSettings.redis_settings = redis_settings
    return WorkerSettings
