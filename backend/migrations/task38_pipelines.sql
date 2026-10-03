-- Task 38. If a `pipelines` table already exists from an earlier task, merge columns instead.
CREATE TABLE IF NOT EXISTS pipelines (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    current_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every config change is a NEW row; old versions are never overwritten.
CREATE TABLE IF NOT EXISTS pipeline_versions (
    pipeline_id UUID NOT NULL REFERENCES pipelines(id),
    version INT NOT NULL,
    config JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pipeline_id, version)
);

-- pipeline_runs must record which version produced each run.
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS pipeline_id UUID REFERENCES pipelines(id);
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS pipeline_version INT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS retrieval_latency_ms INT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_runs_pipeline ON pipeline_runs (pipeline_id, created_at DESC);
