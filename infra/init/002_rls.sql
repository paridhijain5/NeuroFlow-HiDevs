-- Row Level Security: isolate data per pipeline.
-- The app sets the context per transaction:  SET LOCAL app.pipeline_id = '<uuid>';
-- Superusers (the 'neuroflow' admin role) bypass RLS, so the API should connect as
-- (or SET ROLE to) neuroflow_app, which RLS applies to.

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'neuroflow_app') THEN
    CREATE ROLE neuroflow_app NOLOGIN;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO neuroflow_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO neuroflow_app;

CREATE OR REPLACE FUNCTION current_pipeline_id() RETURNS uuid
LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.pipeline_id', true), '')::uuid $$;

ALTER TABLE pipelines     ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents     ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks        ENABLE ROW LEVEL SECURITY;
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_pairs ENABLE ROW LEVEL SECURITY;

CREATE POLICY pipeline_isolation ON pipelines
  USING (id = current_pipeline_id());
CREATE POLICY pipeline_isolation ON documents
  USING (pipeline_id = current_pipeline_id());
CREATE POLICY pipeline_isolation ON pipeline_runs
  USING (pipeline_id = current_pipeline_id());
-- Child tables inherit through their (already RLS-filtered) parents.
CREATE POLICY pipeline_isolation ON chunks
  USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = chunks.document_id));
CREATE POLICY pipeline_isolation ON evaluations
  USING (EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.id = evaluations.run_id));
CREATE POLICY pipeline_isolation ON training_pairs
  USING (EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.id = training_pairs.run_id));
-- finetune_jobs has no pipeline_id (global admin resource), so RLS is not applied.
