-- Task 39. training_pairs was created in Task 37 (run_id, prompt, completion, overall_score).
ALTER TABLE training_pairs ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION;
UPDATE training_pairs SET quality_score = overall_score WHERE quality_score IS NULL;
ALTER TABLE training_pairs ADD COLUMN IF NOT EXISTS included_in_job UUID;

CREATE TABLE IF NOT EXISTS finetune_jobs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | submitted | running | succeeded | failed | cancelled
    base_model TEXT NOT NULL,
    domain TEXT NOT NULL,                        -- becomes the router task_type
    provider_job_id TEXT,                        -- e.g. ftjob-...
    fine_tuned_model TEXT,                       -- e.g. ft:gpt-4o-mini:org::abc
    mlflow_run_id TEXT,
    mlflow_run_url TEXT,
    training_pair_count INT,
    dataset_path TEXT,
    metrics JSONB DEFAULT '{}'::jsonb,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
