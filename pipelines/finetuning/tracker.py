"""MLflow experiment tracking + model registry for fine-tuning jobs.
Tracking server: MLFLOW_TRACKING_URI (default http://localhost:5000). Start one with:  mlflow ui --port 5000
"""
from __future__ import annotations

import os
from statistics import mean
from typing import Any
from uuid import UUID

import mlflow
from mlflow.tracking import MlflowClient

EXPERIMENT = "neuroflow-finetuning"


def _configure() -> None:
    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment(EXPERIMENT)


def start_training_job(job_id: UUID | str, pairs: list, base_model: str, jsonl_path: str | None = None) -> str:
    """Every fine-tuning job is an MLflow run. Returns the MLflow run_id."""
    _configure()
    dates = [p.created_at for p in pairs]
    min_date, max_date = min(dates).date(), max(dates).date()
    with mlflow.start_run(run_name=f"finetune-{job_id}") as run:
        mlflow.log_params({
            "base_model": base_model,
            "training_pair_count": len(pairs),
            "avg_quality_score": round(mean([p.quality_score for p in pairs]), 4),
            "date_range": f"{min_date} to {max_date}",
        })
        mlflow.log_artifact(jsonl_path or f"training_data/{job_id}.jsonl")   # training data as artifact
        return run.info.run_id


def log_job_metrics(run_id: str, *, training_loss: float | None, validation_loss: float | None,
                    trained_tokens: int | None) -> None:
    """After the job completes (resumes the original run)."""
    _configure()
    metrics = {"training_loss": training_loss, "validation_loss": validation_loss,
               "training_token_count": trained_tokens}
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({k: float(v) for k, v in metrics.items() if v is not None})


class _RemoteFineTunedModel(mlflow.pyfunc.PythonModel):
    """Placeholder pyfunc: the real weights live at the provider; this entry just records where."""

    def predict(self, context, model_input, params=None):
        raise NotImplementedError("Remote fine-tuned model - call it through the ModelRouter / provider API.")


def register_model(run_id: str, job_id: UUID | str, model_info: dict[str, Any]) -> str:
    """Registers neuroflow-finetune-{job_id} from runs:/{run_id}/model.
    A fine-tuned OpenAI model has no weights to store, so we log a tiny placeholder MLflow model (plus
    model_info.json with the provider job id + model name) for the registry entry to point at."""
    _configure()
    with mlflow.start_run(run_id=run_id):
        try:
            mlflow.pyfunc.log_model(name="model", python_model=_RemoteFineTunedModel(),
                                    pip_requirements=["mlflow"], metadata=model_info)      # MLflow >= 3
        except TypeError:
            mlflow.pyfunc.log_model(artifact_path="model", python_model=_RemoteFineTunedModel(),
                                    pip_requirements=["mlflow"], metadata=model_info)      # MLflow 2.x
        mlflow.log_dict(model_info, "model_info.json")
    mv = mlflow.register_model(f"runs:/{run_id}/model", f"neuroflow-finetune-{job_id}")
    return str(mv.version)


def mark_run_failed(run_id: str, error: str) -> None:
    _configure()
    client = MlflowClient()
    client.set_tag(run_id, "error", error[:500])
    client.set_terminated(run_id, status="FAILED")


def run_url(run_id: str) -> str:
    _configure()
    exp_id = MlflowClient().get_run(run_id).info.experiment_id
    base = os.getenv("MLFLOW_UI_URL") or (
        os.getenv("MLFLOW_TRACKING_URI", "") if os.getenv("MLFLOW_TRACKING_URI", "").startswith("http")
        else "http://localhost:5000")
    return f"{base.rstrip('/')}/#/experiments/{exp_id}/runs/{run_id}"
