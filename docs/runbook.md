# NeuroFlow Architecture Runbook

*What to do at 3 AM when something breaks.* Work top-to-bottom in each section; stop when the check
that fails tells you the cause. Record what you did in the incident channel.

## 0. Architecture at a glance

```
Client / SDK ──► API (FastAPI, Railway) ──► Redis (cache, queue, rate limits)
                    │  │                        ▲
                    │  └─► Postgres + pgvector ◄┤
                    │         (docs, chunks, runs, evaluations)
                    ├─► LLM providers (OpenAI / Anthropic) via circuit breaker
                    └─► Worker (ingestion + evaluation jobs, consumes Redis queue)
MLflow (experiments, fine-tuning runs) ── Postgres      Jaeger/OTLP (traces)   Prometheus (/metrics)
```

| Component | Where to look | Health signal |
|---|---|---|
| API | Railway logs, `GET /health`, `GET /metrics` | `/health` all green |
| Worker | Railway logs | queue depth falling |
| Postgres | `railway connect postgres` | `SELECT 1`, disk < 80% |
| Redis | `redis-cli -u $REDIS_URL` | `PING` → `PONG`, memory < limit |
| MLflow | `https://<mlflow>.up.railway.app` | UI loads |
| Traces | Jaeger UI | spans present |

**Severity:** SEV1 = platform down / data loss risk (page now); SEV2 = degraded quality or latency
(respond < 30 min); SEV3 = single-feature issue (next business day).

**Always first:** `curl -s https://<your-app>/health | jq` and check the latest deploy in Railway.
If a bad deploy is the cause, follow *Rollback procedure* in `docs/deployment.md`.

---

## Incident 1: High query latency (P95 > 10 s)

**Symptoms:** users report slow answers; Prometheus P95 for the query endpoint > 10 s; SEV2.

**Check**
1. **Jaeger traces:** search service `neuroflow-api`, operation `POST /query`, sort by duration. Find which span dominates (embedding, vector search, rerank, LLM generation).
2. **Redis memory and cache hit rate:**
   ```bash
   redis-cli -u $REDIS_URL INFO memory | grep -E "used_memory_human|maxmemory_human|evicted"
   redis-cli -u $REDIS_URL INFO stats  | grep -E "keyspace_hits|keyspace_misses"
   ```
   Hit rate = hits / (hits + misses). A sudden drop or non-zero `evicted_keys` means the cache is thrashing.
3. **Postgres slow queries (`pg_stat_statements`):**
   ```sql
   SELECT left(query, 80) AS query, calls,
          round(mean_exec_time::numeric, 1) AS mean_ms,
          round(total_exec_time::numeric, 0) AS total_ms
   FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;
   ```
   Also check for lock waits: `SELECT pid, state, wait_event, now()-query_start AS running, left(query,60) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY running DESC;`
4. LLM provider slow? Look at the LLM-call span and the provider status page.

**Remediation**
- **Flush the Redis query cache** (only our keys; avoid `FLUSHALL`, which also wipes queues):
  ```bash
  redis-cli -u $REDIS_URL --scan --pattern 'nf:qcache:*' | xargs -r -n 100 redis-cli -u $REDIS_URL DEL
  ```
- **Add missing indexes** (non-blocking): `CREATE INDEX CONCURRENTLY idx_<table>_<col> ON <table> (<col>);`. For vector search confirm the HNSW index exists: `\d chunks`.
- **Scale API replicas:** Railway dashboard → API service → Settings → Scaling → increase replicas.
- If the LLM is the slow span, lower `TOP_K_AFTER_RERANK` or switch provider (`PROMPT`/provider env var), then redeploy.

**Verify:** P95 back under 4 s for 15 minutes; `locust` smoke test from `docs/deployment.md`.

---

## Incident 2: Evaluation scores degrading

**Symptoms:** nightly quality gate fails / Slack alert; `GET /evaluations` shows falling faithfulness, relevance or MRR; SEV2.

**Check**
1. **Which pipeline and which metric?** `curl -s "https://<app>/evaluations" | jq` and group by `pipeline_id`; note when the drop started.
2. **Recently ingested documents:** low-quality input (scans, empty text, boilerplate, duplicates) causes low-quality retrieval.
   ```sql
   SELECT id, filename, status, created_at FROM documents ORDER BY created_at DESC LIMIT 20;
   SELECT document_id, count(*), avg(length(text)) FROM chunks GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
   ```
3. **MLflow:** open the fine-tuning experiment; did a new model/run get promoted around the time scores dropped? Compare params and metrics with the previous run.
4. Did any quality parameter change (`CHUNK_SIZE`, `DENSE_WEIGHT`, `PROMPT_VARIANT`, ...)? Check recent Railway variable changes.

**Remediation**
- **Revert the last fine-tuned model:** point the pipeline at the previous model version (previous MLflow run/registered version), redeploy, re-run evaluation.
- **Revert recent config changes** to the last known-good values (see `evaluation/quality_final.json`).
- **Inspect/purge bad training or ingestion data:** mark offending documents `archived` (retention job removes their chunks), fix the source, re-ingest.

**Verify:** `python evaluation/measure_quality.py --out /tmp/check.json` meets the targets in `evaluation/quality_final.json`.

---

## Incident 3: LLM provider circuit breaker open

**Symptoms:** queries fail fast with 503; logs show `circuit breaker open`; `GET /health` reports the breaker `open`; SEV1/SEV2 depending on scope.

**Check**
1. `curl -s https://<app>/health | jq '.checks'`: confirm which provider's breaker is `open` and since when.
2. **Provider status page** (OpenAI: status.openai.com, Anthropic: status.anthropic.com): is there an outage or rate-limit incident?
3. Logs: are failures 429 (our quota), 401 (bad/expired key), or 5xx/timeouts (provider side)?

**Remediation**
- Provider outage: **wait for the recovery timeout**; the breaker moves to half-open and probes automatically. Enable the fallback provider if configured (`ANTHROPIC_API_KEY`).
- Quota/key problem: fix the key or limits in the provider dashboard and update the Railway variable.
- Once the provider is healthy, **reset manually** instead of waiting:
  ```bash
  curl -X POST https://<app>/admin/circuit-breaker/reset -H "Authorization: Bearer $ADMIN_TOKEN"
  ```

**Verify:** `/health` shows the breaker `closed`; a test query succeeds; error rate returns to baseline.

---

## Incident 4: Ingestion queue depth > 100

**Symptoms:** documents stuck in `queued`/`processing`; alert on queue depth; SEV2.

**Check**
1. `curl -s https://<app>/health | jq '.checks.queue'`: current depth.
2. **Worker logs** (Railway → worker service → Logs): tracebacks, OOM kills, repeated retries, provider errors (embedding API failing?).
3. Is the worker running at all? Railway shows restarts/crash loops.
4. Stuck jobs in Redis: `redis-cli -u $REDIS_URL LLEN <queue_key>` and look for jobs held in a processing/in-flight list for hours.

**Remediation**
- **Restart worker containers:** Railway → worker → Deployments → Redeploy (or restart).
- **Clear stuck jobs:** move in-flight jobs older than the visibility timeout back to the queue, or delete poison messages that crash the worker (note their IDs first, mark those documents `failed`).
- Scale the worker (more replicas) if the backlog is legitimate load.

**Verify:** queue depth trending to 0; a test ingest reaches `complete` (`python scripts/verify_production.py ...`).

---

## Incident 5: Database disk usage > 80%

**Symptoms:** disk alert; Railway volume > 80%; risk of write failures (SEV1 if > 95%).

**Check**
1. **Which table is growing fastest:**
   ```sql
   SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total
   FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
   ```
   Compare to yesterday's numbers; usual suspects: `chunks` (vectors), `pipeline_runs`, `evaluations`.
2. **Is cleanup running?** Search logs for `retention_complete` (daily, 03:00 UTC). Count what is eligible:
   ```sql
   SELECT count(*) FROM pipeline_runs WHERE status='complete' AND created_at < now() - interval '90 days';
   SELECT count(*) FROM evaluations   WHERE created_at < now() - interval '180 days';
   SELECT count(*) FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE status='archived');
   ```

**Remediation**
- **Run the data retention job manually:**
  ```bash
  railway run --service api python -m backend.jobs.retention
  ```
- Reclaim space after large deletes: `VACUUM (ANALYZE) <table>;` (use `VACUUM FULL` only in a maintenance window: it locks the table).
- If still > 80%: archive/delete unneeded documents, or **increase the volume size** in Railway → Postgres → Settings → Volume.
- Fix the root cause: why wasn't the scheduled job running? (Scheduler not started? Errors in logs?)

**Verify:** disk < 70%; `retention_complete` logged with expected counts; the daily job's next run is scheduled.

---

## Data retention policy

A daily APScheduler job (`backend/jobs/retention.py`, 03:00 UTC) deletes:

| Data | Rule |
|---|---|
| `pipeline_runs` | older than 90 days, `status = 'complete'`, and no associated `evaluations` row |
| `evaluations` | older than 180 days |
| `chunks` | belonging to documents with `status = 'archived'` |

Deleted counts are logged via structlog (`retention_complete`). Run manually: `python -m backend.jobs.retention`.
Start it in the app with `start_scheduler(engine)`.
