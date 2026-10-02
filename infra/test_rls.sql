-- Run as the admin user:  docker compose exec postgres psql -U neuroflow -d neuroflow -f /dev/stdin < test_rls.sql
BEGIN;
INSERT INTO pipelines (id, name, config) VALUES
  ('00000000-0000-0000-0000-00000000000a','rls-a','{}'),
  ('00000000-0000-0000-0000-00000000000b','rls-b','{}');
INSERT INTO documents (id, filename, source_type, content_hash, pipeline_id) VALUES
  ('00000000-0000-0000-0000-0000000000a1','a.txt','text','hash-a','00000000-0000-0000-0000-00000000000a'),
  ('00000000-0000-0000-0000-0000000000b1','b.txt','text','hash-b','00000000-0000-0000-0000-00000000000b');
INSERT INTO chunks (document_id, content, chunk_index, token_count) VALUES
  ('00000000-0000-0000-0000-0000000000a1','chunk of A',0,3),
  ('00000000-0000-0000-0000-0000000000b1','chunk of B',0,3);

SET LOCAL ROLE neuroflow_app;
SELECT 'no context (expect 0)' AS test, count(*) FROM chunks;
SET LOCAL app.pipeline_id = '00000000-0000-0000-0000-00000000000a';
SELECT 'pipeline A (expect 1, "chunk of A")' AS test, content FROM chunks;
SET LOCAL app.pipeline_id = '00000000-0000-0000-0000-00000000000b';
SELECT 'pipeline B (expect 1, "chunk of B")' AS test, content FROM chunks;
ROLLBACK;  -- leaves no test data behind
