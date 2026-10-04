-- Reference schema (adapt if your earlier tasks already created these tables).
CREATE TABLE IF NOT EXISTS evaluations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES pipeline_runs(id),
    faithfulness DOUBLE PRECISION,
    answer_relevance DOUBLE PRECISION,
    context_precision DOUBLE PRECISION,
    context_recall DOUBLE PRECISION,
    overall_score DOUBLE PRECISION,
    user_rating INT CHECK (user_rating BETWEEN 1 AND 5),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS training_pairs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES pipeline_runs(id),
    prompt TEXT,
    completion TEXT,
    overall_score DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT now()
);
