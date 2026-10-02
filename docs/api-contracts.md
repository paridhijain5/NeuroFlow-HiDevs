# NeuroFlow REST API Contracts (v1)

**Conventions**
- Base path `/v1`. JSON bodies, UTF-8.
- **Auth:** `Authorization: Bearer <api_key>` on every endpoint except `/health`.
- **Rate limits:** returned via `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `Retry-After` on 429.
- **Error envelope:** `{"error": {"code": "string", "message": "string", "request_id": "uuid"}}`
- Common errors: `400 invalid_request`, `401 unauthorized`, `403 forbidden`, `404 not_found`, `409 conflict`, `413 payload_too_large`, `415 unsupported_media_type`, `422 validation_error`, `429 rate_limited`, `500 internal_error`, `503 provider_unavailable`.

---

## POST /ingest
File or URL ingestion (async).
- **Auth:** required. **Rate limit:** 30 req/min.
- **Request:** `multipart/form-data` with `file` (PDF, DOCX, PNG/JPG, CSV; max 50 MB), **or** JSON:
```json
{"url": "https://example.com/article", "metadata": {"tag": "docs"}, "pipeline_id": "uuid (optional)"}
```
- **Response `202`:**
```json
{"document_id": "uuid", "job_id": "uuid", "status": "queued"}
```
- **Errors:** 400 (neither file nor url), 413 (file too large), 415 (unsupported type), 422 (bad URL), 429.

## POST /query
Executes a RAG query.
- **Auth:** required. **Rate limit:** 60 req/min.
- **Request:**
```json
{"query": "string (1-4000 chars)", "pipeline_id": "uuid (optional)", "filters": {"document_ids": ["uuid"], "modality": ["pdf","image"], "created_after": "ISO-8601"}, "top_k": 8, "stream": true}
```
- **Response `202`:**
```json
{"query_id": "uuid", "stream_url": "/v1/query/{query_id}/stream", "status": "retrieving"}
```
- **Errors:** 400, 404 (pipeline not found), 422, 429, 503 (no provider available).

## GET /query/{query_id}/stream
Server-Sent Events stream.
- **Auth:** required. **Rate limit:** 60 req/min. Supports `Last-Event-ID`.
- **Events:** `retrieval` → `{"chunks":[{"id","document_id","score","snippet"}]}`; `token` → `{"text":"..."}`; `done` → `{"generation_id":"uuid","model":"string","tokens_in":int,"tokens_out":int,"citations":[{"chunk_id","document_id"}]}`; `error` → error envelope.
- **Errors:** 404 (unknown query_id), 410 (stream expired, 10 min TTL).

## GET /evaluations
Paginated evaluation results.
- **Auth:** required. **Rate limit:** 120 req/min.
- **Query params:** `page` (default 1), `page_size` (default 20, max 100), `model`, `pipeline_id`, `from`, `to`, `min_faithfulness`.
- **Response `200`:**
```json
{"items":[{"id":"uuid","generation_id":"uuid","faithfulness":0.92,"answer_relevance":0.88,"context_precision":0.75,"context_recall":0.81,"model":"string","created_at":"ISO-8601"}],"page":1,"page_size":20,"total":432}
```
- **Errors:** 400 (bad params), 401, 429.

## GET /evaluations/aggregate
Rolling quality metrics.
- **Auth:** required. **Rate limit:** 60 req/min.
- **Query params:** `window` (`1h|24h|7d|30d`, default `24h`), `group_by` (`model|pipeline`), optional filters.
- **Response `200`:**
```json
{"window":"24h","groups":[{"key":"gpt-tier-2","count":310,"faithfulness_avg":0.87,"answer_relevance_avg":0.9,"context_precision_avg":0.72,"context_recall_avg":0.79,"p50_latency_ms":1800}]}
```
- **Errors:** 400 (invalid window/group_by), 401, 429.

## POST /pipelines
Create a named pipeline configuration.
- **Auth:** required. **Rate limit:** 20 req/min.
- **Request:**
```json
{"name":"legal-docs","config":{"chunking":{"strategy":"semantic","max_tokens":512,"overlap":64},"embedding_model":"string","retrieval":{"top_k":50,"rerank_top_k":8,"rrf_k":60},"routing":{"tier":"auto"}}}
```
- **Response `201`:** `{"id":"uuid","name":"legal-docs","config":{...},"created_at":"ISO-8601"}`
- **Errors:** 409 (name exists), 422 (invalid config), 429.

## GET /pipelines/{id}/runs
Pipeline execution history.
- **Auth:** required. **Rate limit:** 120 req/min.
- **Query params:** `page`, `page_size`, `status`.
- **Response `200`:**
```json
{"items":[{"run_id":"uuid","query_id":"uuid","status":"completed","latency_ms":2140,"model":"string","created_at":"ISO-8601"}],"page":1,"page_size":20,"total":57}
```
- **Errors:** 404 (pipeline not found), 401, 429.

## POST /finetune/jobs
Submit a fine-tuning job.
- **Auth:** required (admin scope). **Rate limit:** 5 req/hour.
- **Request:**
```json
{"base_model":"string","min_faithfulness":0.8,"min_user_rating":4,"max_examples":5000,"hyperparameters":{"epochs":3,"learning_rate":1e-5}}
```
- **Response `202`:** `{"job_id":"uuid","status":"preparing","training_examples":1240,"mlflow_run_id":"string"}`
- **Errors:** 400, 403, 409 (job already running), 422 (fewer than 100 qualifying examples), 429.

## GET /finetune/jobs/{id}
- **Auth:** required. **Rate limit:** 120 req/min.
- **Response `200`:**
```json
{"job_id":"uuid","status":"running|succeeded|failed","base_model":"string","result_model":"string|null","metrics":{"train_loss":0.41,"eval_win_rate_vs_base":0.62},"mlflow_run_id":"string","created_at":"ISO-8601"}
```
- **Errors:** 404, 401, 429.

## GET /health
- **Auth:** none. **Rate limit:** none.
- **Response `200`:** `{"status":"ok","postgres":"up","redis":"up","version":"string"}` · **`503`** with the failing dependency marked `down`.

## GET /metrics
- **Auth:** required (metrics scope) or network-restricted. **Rate limit:** 30 req/min.
- **Response `200`:** Prometheus text format (`text/plain; version=0.0.4`): request counts/latency, queue depth, ingestion duration, token usage, evaluation score gauges.
