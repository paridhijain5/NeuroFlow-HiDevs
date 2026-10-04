"""Single place to adapt the tests to YOUR NeuroFlow API.

Everything is overridable via env vars. If a route or response field differs from
what is assumed here, change it in this file only.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
TEST_DOC = FIXTURES / "test_doc.pdf"

BASE_URL = os.getenv("NEUROFLOW_URL", "http://localhost:8000")
CLIENT_ID = os.getenv("NEUROFLOW_CLIENT_ID", "demo")          # needs scopes: query, ingest, admin
CLIENT_SECRET = os.getenv("NEUROFLOW_CLIENT_SECRET", "demo-secret")

# ---- routes ----
AUTH_TOKEN = "/auth/token"
HEALTH = "/health"
INGEST = "/ingest"
DOC_STATUS = "/documents/{doc_id}"            # -> {"status": "complete"|"failed"|...}
DOC_CHUNKS = "/documents/{doc_id}/chunks"     # -> [{"chunk_id","text"}] or {"chunks":[...]}
QUERY = "/query"                              # -> {"run_id": ...}
RUN = "/runs/{run_id}"                        # -> {"generation": str, "chunks_used": int, ...}
RUN_EVAL = "/runs/{run_id}/evaluation"        # -> {"overall_score": float, ...}
PIPELINES = "/pipelines"                      # POST {"name","config":{...}} -> {"pipeline_id"|"id"}
PIPELINE_COMPARE = "/pipelines/compare"       # POST {"pipeline_ids":[..],"query":..} -> {"results":[...]}
FINETUNE_JOBS = "/finetune/jobs"              # POST -> {"job_id","status","output_path"}
FINETUNE_JOB = "/finetune/jobs/{job_id}"
RETRIEVE = "/retrieve"                        # POST {"query","mode","top_k"} -> {"results":[{"chunk_id"}]}
EVALUATIONS = "/evaluations"

# ---- response field names ----
CHUNK_ID_KEY = "chunk_id"
CHUNK_TEXT_KEY = "text"
COMPARE_RESULT_KEYS = {"pipeline_id", "generation", "chunks_used"}  # schema check for test 6

# ---- test knobs ----
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
RATE_WINDOW_SECONDS = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
SKIP_RATE_WAIT = os.getenv("NEUROFLOW_SKIP_RATE_WAIT") == "1"
CIRCUIT_RECOVERY_TIMEOUT = int(os.getenv("CIRCUIT_RECOVERY_TIMEOUT", "30"))
MOCK_LLM_CONTROL = os.getenv("MOCK_LLM_CONTROL", "http://localhost:9999/__control")
DATABASE_URL = os.getenv("DATABASE_URL", "")   # sync SQLAlchemy URL, used by test 7
RETRIEVAL_MODES = ["dense", "sparse", "hybrid", "hybrid_rerank"]
