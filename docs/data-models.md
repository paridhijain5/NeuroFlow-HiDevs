# Data Models (Postgres)

| Table | Key columns |
|-------|-------------|
| `documents` | id (uuid), source_type, source_uri, content_hash (unique), status (`queued/processing/ready/failed`), metadata jsonb, created_at |
| `chunks` | id, document_id FK, content, modality, page, token_count, embedding vector(1536), tsv tsvector, metadata jsonb |
| `pipelines` | id, name (unique), config jsonb (chunker, embedder, retrieval params, model tier), created_at |
| `pipeline_runs` | id, pipeline_id FK, query_id FK, status, latency_ms, created_at |
| `queries` | id, pipeline_id, query_text, filters jsonb, created_at |
| `generations` | id, query_id FK, model, prompt, output, context_chunk_ids uuid[], tokens_in, tokens_out, latency_ms, user_rating smallint null |
| `evaluations` | id, generation_id FK, faithfulness, answer_relevance, context_precision, context_recall (all float 0-1), judge_model, created_at |
| `finetune_jobs` | id, base_model, status, training_examples, mlflow_run_id, result_model, metrics jsonb, created_at |

Indexes: HNSW on `chunks.embedding` (cosine), GIN on `chunks.tsv`, GIN on `chunks.metadata`, btree on `evaluations.created_at`.
