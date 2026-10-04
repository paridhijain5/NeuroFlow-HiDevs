# NeuroFlow — Cloud Deployment (Railway)

**Target:** Railway (managed Postgres + pgvector, managed Redis, Docker deploys, multi-service).
**Services:** `postgres` → `redis` → `mlflow` → `api` → `worker`. Deploy in that order and verify each before adding the next.

> Replace `<...>` placeholders with your values. Commands marked ADAPT depend on your repo layout.

## 0. Prerequisites
- Railway account + GitHub repo connected
- Node.js 18+, Python 3.11
- `.env.example` reviewed (every variable is documented there)

```bash
npm install -g @railway/cli
railway login
railway init          # create project "neuroflow"
bash scripts/generate_secrets.sh   # prints secrets; paste into Railway Variables
```

## 1. PostgreSQL + pgvector
1. Railway dashboard → **New → Template → search "pgvector"** (Postgres image with the pgvector extension). If unavailable, add Postgres and use image `pgvector/pgvector:pg16`.
2. Set `POSTGRES_PASSWORD` (from the secrets script).
3. Enable the extension and create the MLflow database:
   ```bash
   railway connect postgres
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE DATABASE mlflow;
   \dx   -- verify "vector" is listed
   ```
4. **Verify:** `SELECT 1;` succeeds.

## 2. Redis
1. **New → Database → Redis**; set `REDIS_PASSWORD`.
2. **Verify:** `railway connect redis` then `PING` → `PONG`.

## 3. MLflow tracking server
1. **New → Docker Image** → `ghcr.io/mlflow/mlflow:latest`.
2. Start command:
   ```
   mlflow server --host 0.0.0.0 --port $PORT --backend-store-uri ${{Postgres.DATABASE_URL_MLFLOW}} --default-artifact-root /mlflow/artifacts
   ```
   (Create `DATABASE_URL_MLFLOW` as a variable pointing at the `mlflow` database, e.g. `postgresql://postgres:<pw>@postgres.railway.internal:5432/mlflow`.)
3. Attach a **Volume** at `/mlflow/artifacts`; **Settings → Networking → Generate Domain**.
4. **Verify:** open the MLflow domain → UI loads.

## 4. API service
1. **New → GitHub Repo** → select repo, branch `main`. Railway uses `railway.toml` (Dockerfile `backend/Dockerfile`, healthcheck `/health`). ADAPT if paths differ.
2. **Variables** (use Railway references so secrets aren't copied around):

   | Variable | Value |
   |---|---|
   | `ENVIRONMENT` | `production` |
   | `POSTGRES_URL` | `${{Postgres.DATABASE_URL}}` |
   | `REDIS_URL` | `${{Redis.REDIS_URL}}` |
   | `MLFLOW_TRACKING_URI` | `https://<your-mlflow>.up.railway.app` (or the Postgres URL if your code expects the backend store — match `.env.example`) |
   | `OPENAI_API_KEY` | your key |
   | `ANTHROPIC_API_KEY` | optional |
   | `JWT_SECRET_KEY`, `PLUGIN_SECRETS_KEY` | from `generate_secrets.sh` |
   | `LOG_LEVEL` | `INFO` |
   | `SENTRY_DSN`, `OTEL_EXPORTER_OTLP_ENDPOINT` | optional |

3. Run migrations once (ADAPT to your tool):
   ```bash
   railway run --service api alembic upgrade head
   ```
4. **Settings → Networking → Generate Domain**.
5. **Verify:** `curl https://<your-app>.up.railway.app/health` → all checks green.

## 5. Worker service
1. **New → GitHub Repo** (same repo), same variables as the API (use *Shared Variables*).
2. Start command (ADAPT): `python -m backend.worker`. No public domain needed.
3. **Verify:** logs show the worker connected to Redis and idle/consuming.

## 6. Production verification checklist
Run everything automatically:
```bash
pip install requests
python scripts/verify_production.py \
  --base-url https://<your-app>.up.railway.app \
  --mlflow-url https://<your-mlflow>.up.railway.app
```
This writes `docs/production_results.md`. Manual equivalents:

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Health | `curl <URL>/health` | all checks green |
| 2 | Ingest | `curl -F file=@tests/fixtures/test_doc.pdf <URL>/ingest` | status reaches `complete` |
| 3 | Query | `POST /query` about the doc | answer with citations |
| 4 | Evaluation | `curl <URL>/evaluations` | entry with scores |
| 5 | Streaming | `curl -N <URL>/query/{run_id}/stream` | tokens arrive progressively |
| 6 | MLflow | open MLflow URL | experiments visible |
| 7 | Metrics | `curl <URL>/metrics` | all custom metrics present |
| 8 | Load test | see below | see results |

### Load test (10 users, 2 minutes)
```bash
pip install locust
locust -f tests/performance/locustfile.py -H https://<your-app>.up.railway.app \
  --headless -u 10 -r 2 --run-time 2m \
  --csv=docs/load_test/prod --html=docs/load_test/prod_report.html
```
Commit `docs/load_test/`.

### Results (fill in / paste from `docs/production_results.md`)

| Check | Result | Notes |
|---|---|---|
| 1 Health | _pending_ | |
| 2 Ingest | _pending_ | |
| 3 Query | _pending_ | |
| 4 Evaluation | _pending_ | |
| 5 Streaming | _pending_ | |
| 6 MLflow | _pending_ | |
| 7 Metrics | _pending_ | |
| 8 Load test | _pending_ | p50 / p95 / failure rate: |

## 7. Rollback procedure

### 7.1 Redeploy the previous Docker image tag
Images are pushed to GHCR tagged with the commit SHA by `build.yml`.
- **Dashboard (fastest):** service → **Deployments** → previous successful deployment → **⋯ → Redeploy**.
- **By image tag:** set the service source to `ghcr.io/<owner>/<repo>/api:<previous-sha>` and redeploy. Find tags in the repo's **Packages** page or `git log --oneline main`.
- **CLI:** `git revert <bad-sha> && git push origin main` — CI then rebuilds a known-good image.

### 7.2 Reverse database migrations (if the bad release added one)
```bash
railway run --service api alembic current          # note the revision
railway run --service api alembic downgrade -1     # or: downgrade <previous_revision>
```
Take a backup first (`railway connect postgres` → `pg_dump`) when the migration drops/changes data. If a release had no migration, skip this step.

### 7.3 Verify rollback succeeded
1. `curl <URL>/health` → all green.
2. Confirm the running image tag / deployment ID matches the previous version in the dashboard.
3. Re-run `python scripts/verify_production.py ...` → all PASS.
4. Watch logs and Sentry for ~10 minutes for new errors.

## 8. Troubleshooting
- **502 / health failing:** check `PORT` binding (`0.0.0.0:$PORT`) and that migrations ran.
- **pgvector errors:** run `CREATE EXTENSION IF NOT EXISTS vector;` on the *app* database.
- **Service can't reach DB/Redis:** use `*.railway.internal` hostnames and reference variables.
- **MLflow 500:** confirm the `mlflow` database exists and the URL is reachable from the service.
