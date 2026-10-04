"""Locust load test: 3 user types.

Run (50 users, 5 min) - writes tests/performance/load_test_results.json on exit:
  locust -f tests/performance/locustfile.py --headless -u 50 -r 5 --run-time 5m --host http://localhost:8000

Pass criteria: P95 /query latency < 5s AND error rate < 2%  (exit code 1 otherwise).
Run the server with a rate limit high enough for the load test, or 429s count as errors.
"""
import glob
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

from locust import HttpUser, between, events, task

CLIENT_ID = os.getenv("NEUROFLOW_CLIENT_ID", "demo")
CLIENT_SECRET = os.getenv("NEUROFLOW_CLIENT_SECRET", "demo-secret")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
RESULTS_PATH = Path(__file__).resolve().parent / "load_test_results.json"

P95_LIMIT_MS = 5000
ERROR_RATE_LIMIT = 0.02

SAMPLE_QUERIES = [
    "What is the main topic of the document?",
    "Summarize the key contributions described.",
    "What architecture is proposed?",
    "How does attention work?",
    "What datasets were used for evaluation?",
    "What are the main limitations mentioned?",
    "How is positional information encoded?",
    "What results were reported?",
]
TEST_DOCS = sorted(glob.glob(str(FIXTURES / "*.pdf")))


class _AuthedUser(HttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self):
        r = self.client.post(
            "/auth/token",
            json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            name="/auth/token",
        )
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"


class QueryUser(_AuthedUser):
    weight = 7

    @task
    def query_pipeline(self):
        self.client.post("/query", json={"query": random.choice(SAMPLE_QUERIES)}, name="/query")


class IngestUser(_AuthedUser):
    weight = 2

    @task
    def ingest_document(self):
        if not TEST_DOCS:
            return
        path = random.choice(TEST_DOCS)
        with open(path, "rb") as f:
            self.client.post(
                "/ingest",
                files={"file": (os.path.basename(path), f, "application/pdf")},
                name="/ingest",
            )


class AdminUser(_AuthedUser):
    weight = 1

    @task
    def check_evaluations(self):
        self.client.get("/evaluations", name="/evaluations")


@events.quitting.add_listener
def _write_results(environment, **_):
    stats = environment.stats
    q = stats.get("/query", "POST")
    total = stats.total
    p95 = q.get_response_time_percentile(0.95) if q.num_requests else None
    error_rate = total.fail_ratio if total.num_requests else None
    passed = (
        p95 is not None and error_rate is not None
        and p95 < P95_LIMIT_MS and error_rate < ERROR_RATE_LIMIT
    )
    opts = getattr(environment, "parsed_options", None)
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "users": getattr(opts, "num_users", None),
        "spawn_rate": getattr(opts, "spawn_rate", None),
        "run_time_seconds": getattr(opts, "run_time", None),
        "total_requests": total.num_requests,
        "total_failures": total.num_failures,
        "error_rate": error_rate,
        "query": {
            "requests": q.num_requests,
            "failures": q.num_failures,
            "avg_ms": q.avg_response_time if q.num_requests else None,
            "p50_ms": q.get_response_time_percentile(0.50) if q.num_requests else None,
            "p95_ms": p95,
            "p99_ms": q.get_response_time_percentile(0.99) if q.num_requests else None,
        },
        "endpoints": {
            f"{s.method} {s.name}": {
                "requests": s.num_requests, "failures": s.num_failures,
                "p95_ms": s.get_response_time_percentile(0.95) if s.num_requests else None,
            }
            for s in stats.entries.values()
        },
        "thresholds": {"p95_ms_lt": P95_LIMIT_MS, "error_rate_lt": ERROR_RATE_LIMIT},
        "passed": bool(passed),
    }
    RESULTS_PATH.write_text(json.dumps(result, indent=2))
    if not passed:
        environment.process_exit_code = 1
