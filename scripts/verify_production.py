#!/usr/bin/env python3
"""Runs the production verification checklist and writes docs/production_results.md.

Usage:
  pip install requests
  python scripts/verify_production.py --base-url https://your-app.up.railway.app \
      --mlflow-url https://your-mlflow.up.railway.app [--token <JWT>] [--pdf tests/fixtures/test_doc.pdf]

ADAPT: the endpoint constants below if your API paths/payloads differ.
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

INGEST_PATH = "/ingest"                       # POST multipart, field name FILE_FIELD
FILE_FIELD = "file"
STATUS_PATH = "/ingest/{doc_id}"              # GET -> {"status": "complete"}  (ADAPT)
QUERY_PATH = "/query"                         # POST {"query": ...} -> {"run_id": ...}
STREAM_PATH = "/query/{run_id}/stream"        # GET (SSE)
EVAL_PATH = "/evaluations"
TEST_QUERY = "What is this document about?"
TIMEOUT = 60

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--mlflow-url", default="")
    p.add_argument("--token", default="")
    p.add_argument("--pdf", default="tests/fixtures/test_doc.pdf")
    a = p.parse_args()
    base = a.base_url.rstrip("/")
    h = {"Authorization": f"Bearer {a.token}"} if a.token else {}
    run_id = doc_id = None

    # 1. health
    try:
        r = requests.get(f"{base}/health", headers=h, timeout=TIMEOUT)
        record("1. GET /health", r.ok, f"{r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        record("1. GET /health", False, str(e))

    # 2. ingest
    try:
        with open(a.pdf, "rb") as f:
            r = requests.post(f"{base}{INGEST_PATH}", headers=h, files={FILE_FIELD: f}, timeout=TIMEOUT)
        body = r.json() if r.ok else {}
        doc_id = body.get("document_id") or body.get("id") or body.get("doc_id")
        status = body.get("status", "")
        deadline = time.time() + 180
        while doc_id and status != "complete" and time.time() < deadline:
            time.sleep(3)
            s = requests.get(f"{base}{STATUS_PATH.format(doc_id=doc_id)}", headers=h, timeout=TIMEOUT)
            status = s.json().get("status", "") if s.ok else status
        record("2. POST /ingest -> complete", status == "complete", f"doc_id={doc_id} status={status}")
    except Exception as e:  # noqa: BLE001
        record("2. POST /ingest -> complete", False, str(e))

    # 3. query
    try:
        r = requests.post(f"{base}{QUERY_PATH}", headers=h, json={"query": TEST_QUERY}, timeout=120)
        body = r.json() if r.ok else {}
        run_id = body.get("run_id") or body.get("id")
        answer = body.get("answer") or body.get("response") or ""
        cited = bool(body.get("citations") or body.get("sources")) or "[" in answer
        record("3. Query -> cited answer", r.ok and bool(answer) and cited,
               f"{r.status_code} answer_len={len(answer)} cited={cited} run_id={run_id}")
    except Exception as e:  # noqa: BLE001
        record("3. Query -> cited answer", False, str(e))

    # 4. evaluations
    try:
        r = requests.get(f"{base}{EVAL_PATH}", headers=h, timeout=TIMEOUT)
        data = r.json() if r.ok else []
        n = len(data) if isinstance(data, list) else len(data.get("items", data.get("results", [])))
        record("4. GET /evaluations", r.ok and n > 0, f"{r.status_code} entries={n}")
    except Exception as e:  # noqa: BLE001
        record("4. GET /evaluations", False, str(e))

    # 5. streaming
    try:
        if not run_id:
            raise RuntimeError("no run_id from step 3")
        stamps = []
        with requests.get(f"{base}{STREAM_PATH.format(run_id=run_id)}", headers=h, stream=True, timeout=120) as r:
            for line in r.iter_lines():
                if line:
                    stamps.append(time.time())
        spread = (stamps[-1] - stamps[0]) if len(stamps) > 1 else 0
        record("5. Streaming", len(stamps) > 1, f"{len(stamps)} events over {spread:.2f}s")
    except Exception as e:  # noqa: BLE001
        record("5. Streaming", False, str(e))

    # 6. MLflow
    try:
        r = requests.get(a.mlflow_url, timeout=TIMEOUT) if a.mlflow_url else None
        record("6. MLflow UI", bool(r and r.ok), f"{r.status_code}" if r else "pass --mlflow-url")
    except Exception as e:  # noqa: BLE001
        record("6. MLflow UI", False, str(e))

    # 7. Prometheus metrics
    try:
        r = requests.get(f"{base}/metrics", headers=h, timeout=TIMEOUT)
        names = sorted({l.split("{")[0].split(" ")[0] for l in r.text.splitlines() if l and not l.startswith("#")})
        record("7. GET /metrics", r.ok and len(names) > 0, f"{len(names)} metric series; sample: {', '.join(names[:8])}")
    except Exception as e:  # noqa: BLE001
        record("7. GET /metrics", False, str(e))

    out = [f"# Production verification results\n", f"- URL: {base}", f"- Run at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n",
           "| Check | Result | Detail |", "|---|---|---|"]
    out += [f"| {n} | {'PASS' if ok else 'FAIL'} | {d.replace('|', '/')} |" for n, ok, d in results]
    out.append("\n8. Load test: see `docs/load_test/` (run the locust command in deployment.md).")
    Path("docs").mkdir(exist_ok=True)
    Path("docs/production_results.md").write_text("\n".join(out) + "\n")
    print("\nWrote docs/production_results.md")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
