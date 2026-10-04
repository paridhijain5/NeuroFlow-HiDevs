"""Offline self-test for the Task 42 checklist (in-memory span exporter, real prometheus_client, fakeredis).
Run from the repo root:   python -m backend.test_task42
Optional: PROMTOOL=/path/to/promtool python -m backend.test_task42   (also lints alerts.yml / prometheus.yml)
"""
import asyncio, json, os, re, shutil, subprocess, time
from pathlib import Path

import fakeredis, httpx, yaml
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from backend.monitoring import anomaly, metrics, tracing
from backend.monitoring.demo_pipeline import (FakeDB, FakeProvider, ingest_document, run_eval_worker_once,
                                              run_query_flow)
from backend.monitoring.tracing import SPANS
from backend.resilience.backpressure import check_backpressure
from backend.resilience.circuit_breaker import CircuitBreaker
from backend.task42_demo_app import create_app
from evaluation.judge import EvaluationJudge
from evaluation.mock_llm import MockEmbedder, MockLLM

ROOT = Path(__file__).resolve().parent.parent
EXPORTER = InMemorySpanExporter()


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    assert cond, name


def val(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def spans_by_name():
    out = {}
    for s in EXPORTER.get_finished_spans():
        out.setdefault(s.name, []).append(s)
    return out


class Clock:
    def __init__(self, now=1_800_000_000.0): self.now = now
    def __call__(self): return self.now


async def test_traces():
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    db = FakeDB()
    judge = EvaluationJudge(db, MockLLM(), MockEmbedder())

    await ingest_document("pdf", "legal", "Liability is capped at the fees paid. " * 20)
    out = await run_query_flow(redis, db, FakeProvider(), "legal", "What is the liability cap?")
    check("query flow completes (tokens + done)", [e["type"] for e in out["events"]][-1] == "done")
    res = await run_eval_worker_once(redis, judge)
    check("eval worker evaluated the run from the queue", res is not None and 0 < res.overall_score <= 1)

    S = spans_by_name()
    by_id = {s.context.span_id: s for s in EXPORTER.get_finished_spans()}

    def parent(s):
        return by_id.get(s.parent.span_id) if s.parent else None

    required = [SPANS.INGEST, SPANS.EXTRACT.format(source_type="pdf"), SPANS.CHUNK, SPANS.EMBED, SPANS.WRITE_DB,
                SPANS.RETRIEVAL, SPANS.DENSE, SPANS.SPARSE, SPANS.METADATA, SPANS.FUSION, SPANS.RERANK, SPANS.ASSEMBLE,
                SPANS.GENERATION, SPANS.PROMPT_BUILD, SPANS.LLM_CALL, SPANS.CITATION_PARSE, SPANS.LOG_RUN,
                SPANS.JUDGE, SPANS.FAITHFULNESS, SPANS.ANSWER_RELEVANCE, SPANS.CONTEXT_PRECISION, SPANS.CONTEXT_RECALL]
    check("all 22 required span names are emitted", all(n in S for n in required),
          [n for n in required if n not in S])

    ing = S[SPANS.INGEST][0]
    check("ingestion: process > extract.pdf / chunk / embed / write_db",
          all(parent(S[n][0]) is ing for n in (SPANS.EXTRACT.format(source_type="pdf"), SPANS.CHUNK, SPANS.EMBED, SPANS.WRITE_DB)))
    req = S["demo.request"][0]
    ret, gen = S[SPANS.RETRIEVAL][0], S[SPANS.GENERATION][0]
    check("one trace: request > retrieval.pipeline and generation.pipeline",
          parent(ret) is req and parent(gen) is req and ret.context.trace_id == gen.context.trace_id == req.context.trace_id)
    check("retrieval.pipeline > dense / sparse / metadata / fusion / rerank / assemble",
          all(parent(S[n][0]) is ret for n in (SPANS.DENSE, SPANS.SPARSE, SPANS.METADATA, SPANS.FUSION, SPANS.RERANK, SPANS.ASSEMBLE)))
    check("generation.pipeline > prompt_build / llm_call / citation_parse / log_run(x2)",
          all(parent(S[n][0]) is gen for n in (SPANS.PROMPT_BUILD, SPANS.LLM_CALL, SPANS.CITATION_PARSE))
          and len(S[SPANS.LOG_RUN]) == 2 and all(parent(s) is gen for s in S[SPANS.LOG_RUN]))
    judge_span = S[SPANS.JUDGE][0]
    check("evaluation.judge joins the SAME trace via the queue (child of generation.pipeline)",
          judge_span.context.trace_id == gen.context.trace_id and parent(judge_span) is gen)
    check("judge > faithfulness / answer_relevance / context_precision / context_recall",
          all(parent(S[n][0]) is judge_span for n in (SPANS.FAITHFULNESS, SPANS.ANSWER_RELEVANCE,
                                                       SPANS.CONTEXT_PRECISION, SPANS.CONTEXT_RECALL)))

    non_root = [s for s in EXPORTER.get_finished_spans() if s.name != "demo.request"]
    check("EVERY span carries pipeline_id", all(s.attributes.get("pipeline_id") == "legal" for s in non_root),
          [s.name for s in non_root if s.attributes.get("pipeline_id") != "legal"])
    run_scoped = [SPANS.RETRIEVAL, SPANS.DENSE, SPANS.RERANK, SPANS.GENERATION, SPANS.PROMPT_BUILD, SPANS.LLM_CALL,
                  SPANS.CITATION_PARSE, SPANS.LOG_RUN, SPANS.JUDGE, SPANS.FAITHFULNESS, SPANS.CONTEXT_RECALL]
    run_id = out["run_id"]
    check("run-scoped spans carry run_id", all(s.attributes.get("run_id") == run_id for n in run_scoped for s in S[n]),
          [n for n in run_scoped if any(s.attributes.get("run_id") != run_id for s in S[n])])
    la = S[SPANS.LLM_CALL][0].attributes
    check("llm_call has model / token counts / latency / cost",
          la["model"] == "gpt-4o-mini" and la["input_tokens"] == 1200 and la["output_tokens"] == 180
          and "latency_ms" in la and la["cost_usd"] > 0, dict(la))
    check("retrieval.pipeline has chunk_count + latency_ms + strategy",
          {"chunk_count", "latency_ms", "strategy"} <= set(ret.attributes))
    check("embed has token_count; generation has chunk_count; judge has latency_ms",
          "token_count" in S[SPANS.EMBED][0].attributes and "chunk_count" in gen.attributes
          and "latency_ms" in judge_span.attributes)
    check("metric spans record their score", all("score" in S[n][0].attributes for n in
          (SPANS.FAITHFULNESS, SPANS.ANSWER_RELEVANCE, SPANS.CONTEXT_PRECISION, SPANS.CONTEXT_RECALL)))

    # error path: span marked ERROR, error counter, no eval job
    EXPORTER.clear()
    before_err = val("neuroflow_queries_total", pipeline_id="legal", status="error")
    bad = await run_query_flow(redis, db, FakeProvider(fail=True), "legal", "q")
    llm_span = spans_by_name()[SPANS.LLM_CALL][0]
    check("provider failure: llm_call span has ERROR status + exception event",
          llm_span.status.status_code.name == "ERROR" and any(e.name == "exception" for e in llm_span.events))
    check("provider failure: queries_total{status=error} incremented",
          val("neuroflow_queries_total", pipeline_id="legal", status="error") == before_err + 1
          and bad["events"][-1]["type"] == "error")

    # contextvar attributes + decorator
    EXPORTER.clear()
    with tracing.span("outside"):
        pass
    token = tracing.set_context(pipeline_id="px")
    with tracing.span("inside"):
        pass
    tracing.reset_context(token)

    @tracing.traced("decorated.async", lambda x: {"x": x})
    async def af(x): return x * 2

    @tracing.traced("decorated.sync")
    def sf(): return 1
    assert await af(2) == 4 and sf() == 1
    S = spans_by_name()
    check("set_context() adds pipeline_id to spans started inside only",
          "pipeline_id" not in S["outside"][0].attributes and S["inside"][0].attributes["pipeline_id"] == "px")
    check("@traced works for async + sync functions", "decorated.async" in S and "decorated.sync" in S
          and S["decorated.async"][0].attributes["x"] == 2)


async def test_metrics():
    # --- exact definitions from the task
    spec = {
        "neuroflow_queries_total": (("pipeline_id", "status"), None),
        "neuroflow_ingestion_docs_total": (("source_type",), None),
        "neuroflow_llm_calls_total": (("provider", "model", "task_type"), None),
        "neuroflow_circuit_breaker_trips_total": (("provider",), None),
        "neuroflow_retrieval_latency_seconds": (("strategy",), [0.05, 0.1, 0.25, 0.5, 1.0, 2.5]),
        "neuroflow_generation_latency_seconds": (("model",), [0.5, 1, 2, 5, 10, 30]),
        "neuroflow_llm_cost_usd": (("model",), [0.0001, 0.001, 0.01, 0.1, 1.0]),
        "neuroflow_eval_faithfulness": (("pipeline_id",), None),
        "neuroflow_eval_overall": (("pipeline_id",), None),
        "neuroflow_queue_depth": ((), None),
        "neuroflow_circuit_breakers_open": ((), None),
    }
    objs = {m._name: m for m in (metrics.queries_total, metrics.ingestion_docs_total, metrics.llm_calls_total,
                                 metrics.circuit_breaker_trips, metrics.retrieval_latency, metrics.generation_latency,
                                 metrics.llm_cost, metrics.eval_faithfulness, metrics.eval_overall,
                                 metrics.queue_depth, metrics.active_circuit_breakers_open)}
    ok = True
    for name, (labels, buckets) in spec.items():
        m = objs[name.removesuffix("_total")] if name.removesuffix("_total") in objs else objs[name]
        ok &= tuple(m._labelnames) == labels
        if buckets:
            ok &= [b for b in m._upper_bounds if b != float("inf")] == [float(b) for b in buckets]
    check("all 11 required metrics exist with the exact names, labels and buckets", ok)

    # --- updated where the operation occurs (values moved by the real code paths in test_traces)
    check("queries_total{status=ok} counted by the generator", val("neuroflow_queries_total", pipeline_id="legal", status="ok") >= 1)
    check("llm_calls_total counted (generation + evaluation judge not via router here)",
          val("neuroflow_llm_calls_total", provider="openai", model="gpt-4o-mini", task_type="rag_generation") >= 1)
    check("generation latency histogram observed", val("neuroflow_generation_latency_seconds_count", model="gpt-4o-mini") >= 1)
    cost_n, cost_sum = val("neuroflow_llm_cost_usd_count", model="gpt-4o-mini"), val("neuroflow_llm_cost_usd_sum", model="gpt-4o-mini")
    check("LLM cost histogram observed with the computed cost", cost_n >= 1 and abs(cost_sum / cost_n - 0.000288) < 1e-9, cost_sum / cost_n)
    check("retrieval latency histogram observed (strategy=hybrid)", val("neuroflow_retrieval_latency_seconds_count", strategy="hybrid") >= 1)
    check("ingestion_docs_total{source_type=pdf} counted", val("neuroflow_ingestion_docs_total", source_type="pdf") >= 1)
    for g in ("faithfulness", "answer_relevance", "context_precision", "context_recall", "overall"):
        v = val(f"neuroflow_eval_{g}", pipeline_id="legal")
        assert 0 < v <= 1, g
    check("evaluation gauges set per pipeline by the judge", True)

    # --- rolling average
    for s in (0.9, 0.7):
        metrics.record_evaluation("rollp", faithfulness=s, answer_relevance=s, context_precision=s, context_recall=s, overall=s)
    check("gauges hold a ROLLING average", abs(val("neuroflow_eval_overall", pipeline_id="rollp") - 0.8) < 1e-9)

    # --- circuit breaker transitions
    r, clock = fakeredis.FakeAsyncRedis(decode_responses=True), Clock()
    cb = CircuitBreaker("trip-test", redis=r, clock=clock)

    async def fail():
        try:
            async with cb:
                raise RuntimeError("x")
        except Exception:
            pass

    t0 = val("neuroflow_circuit_breaker_trips_total", provider="trip-test")
    for _ in range(5): await fail()
    check("5th failure opens circuit -> trips counter +1, state gauge 1",
          val("neuroflow_circuit_breaker_trips_total", provider="trip-test") == t0 + 1
          and val("neuroflow_circuit_breaker_state", provider="trip-test") == 1)
    check("active open breakers gauge counts it", val("neuroflow_circuit_breakers_open") >= 1)
    for _ in range(3): await fail()                       # rejected while open -> NOT new trips
    check("calls rejected while open don't count as trips", val("neuroflow_circuit_breaker_trips_total", provider="trip-test") == t0 + 1)
    clock.now += 61
    await fail()                                          # half-open probe fails -> re-opens
    check("half-open failure re-opening counts as a trip", val("neuroflow_circuit_breaker_trips_total", provider="trip-test") == t0 + 2)
    clock.now += 61
    async with cb:
        pass
    check("success in half-open closes it -> state gauge 0, open count drops",
          val("neuroflow_circuit_breaker_state", provider="trip-test") == 0)

    # --- gauges mirrored from Redis + queue depth
    await r.set("circuit:other:state", "open")
    await r.rpush("queue:ingest", *["x"] * 7)
    await metrics.refresh_from_redis(r)
    check("refresh_from_redis: queue_depth + breaker gauges follow shared Redis state",
          val("neuroflow_queue_depth") == 7 and val("neuroflow_circuit_breaker_state", provider="other") == 1)
    await r.rpush("queue:ingest", *["x"] * 5)
    await check_backpressure(r)
    check("queue_depth updated at the point of use (backpressure check)", val("neuroflow_queue_depth") == 12)


async def test_endpoint_and_infra():
    app = create_app(exporter=EXPORTER, simple_spans=True)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    r1 = await c.post("/demo/ingest?source_type=docx")
    r2 = await c.post("/demo/query?pipeline_id=legal")
    await asyncio.sleep(0.2)
    await c.post("/demo/breaker-trip?provider=anthropic")
    resp = await c.get("/metrics")
    text = resp.text
    check("GET /metrics -> 200 Prometheus text format", resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain") and "version=" in resp.headers["content-type"])
    from prometheus_client.parser import text_string_to_metric_families
    families = {f.name: f for f in text_string_to_metric_families(text)}
    need = ["neuroflow_queries", "neuroflow_ingestion_docs", "neuroflow_llm_calls", "neuroflow_circuit_breaker_trips",
            "neuroflow_retrieval_latency_seconds", "neuroflow_generation_latency_seconds", "neuroflow_llm_cost_usd",
            "neuroflow_eval_faithfulness", "neuroflow_eval_overall", "neuroflow_queue_depth", "neuroflow_circuit_breakers_open"]
    check("valid exposition format; all required metric families present and populated",
          all(n in families and families[n].samples for n in need), [n for n in need if n not in families])
    check("demo endpoints work end-to-end", r1.json()["chunks"] > 0 and r2.json()["events"][-1] == "done")

    # ---- alert rules / prometheus config / compose / dashboards
    rules = yaml.safe_load((ROOT / "infra/prometheus/alerts.yml").read_text())["groups"][0]["rules"]
    got = {r["alert"]: (r["expr"], r["for"], r["annotations"]["summary"]) for r in rules}
    check("alerts.yml: the 4 required alerts with the task's expressions and durations",
          got["HighEvaluationFailureRate"][:2] == ('rate(neuroflow_queries_total{status="error"}[5m]) > 0.1', "5m")
          and got["CircuitBreakerOpen"][:2] == ("neuroflow_circuit_breakers_open > 0", "1m")
          and got["EvaluationScoreDegraded"][:2] == ("neuroflow_eval_overall < 0.65", "15m")
          and got["QueueDepthHigh"][:2] == ("neuroflow_queue_depth > 50", "5m"))
    prom = yaml.safe_load((ROOT / "infra/prometheus/prometheus.yml").read_text())
    sc = prom["scrape_configs"][0]
    check("prometheus.yml scrapes http://api:8000/metrics and loads alerts.yml",
          sc["static_configs"][0]["targets"] == ["api:8000"] and sc["metrics_path"] == "/metrics"
          and prom["rule_files"] == ["/etc/prometheus/alerts.yml"])
    comp = yaml.safe_load((ROOT / "infra/docker-compose.observability.yml").read_text())["services"]
    check("compose: prometheus + grafana + jaeger (OTLP :4317)",
          {"prometheus", "grafana", "jaeger"} <= set(comp) and "4317:4317" in comp["jaeger"]["ports"])

    exposed = set(re.findall(r"^(neuroflow_\w+?)(?:\{| )", text, re.M))
    def known(metric):
        return any(metric == e or metric in (e + "_bucket", e + "_sum", e + "_count") for e in exposed) or metric in {
            f"neuroflow_{n}" for n in ("queries_total",)}
    dash_dir = ROOT / "infra/grafana"
    titles, bad = [], []
    for f in ("system-overview.json", "quality-monitor.json"):
        d = json.loads((dash_dir / f).read_text())
        for p in d["panels"]:
            titles.append(p["title"])
            for t in p["targets"]:
                for m in re.findall(r"neuroflow_\w+", t.get("expr", "")):
                    if not known(m):
                        bad.append((f, p["title"], m))
    check("dashboards: required panels exist",
          all(any(w.lower() in t.lower() for t in titles) for w in (
              "Queries per minute", "Retrieval latency", "Generation latency", "LLM cost per hour", "Queue depth",
              "Circuit breaker status", "Rolling 24h", "Overall score trend", "Fine-tuning job history", "Documents ingested per day")), titles)
    check("every PromQL metric used by the dashboards / alerts is actually exposed by /metrics",
          not bad and all(known(m) for r in rules for m in re.findall(r"neuroflow_\w+", r["expr"])), bad)

    pt = os.getenv("PROMTOOL") or shutil.which("promtool")
    if pt:
        for args in (["check", "rules", str(ROOT / "infra/prometheus/alerts.yml")],):
            out = subprocess.run([pt, *args], capture_output=True, text=True)
            check("promtool check rules", out.returncode == 0, (out.stdout + out.stderr).strip()[:200])
    else:
        print("SKIP promtool lint (set PROMTOOL=/path/to/promtool)")


async def test_anomaly():
    stable = [0.80, 0.82, 0.79, 0.81, 0.80, 0.83, 0.78, 0.81, 0.80, 0.82, 0.79]
    a = anomaly.detect_anomaly("p1", stable + [0.60])
    check("anomaly: latest score > 2 std below the mean is flagged", a is not None and a.latest == 0.60 and a.threshold > 0.6)
    check("anomaly: normal score is not flagged", anomaly.detect_anomaly("p1", stable + [0.79]) is None)
    check("anomaly: too few samples / zero variance -> no alert",
          anomaly.detect_anomaly("p1", [0.8] * 5 + [0.1]) is None and anomaly.detect_anomaly("p1", [0.8] * 20 + [0.1]) is None)
    posted = []

    async def post(payload): posted.append(payload)

    async def scores(_): return stable + [0.55]

    async def sugg(_): return [{"path": "retrieval.top_k_after_rerank", "current": 8, "suggested": 6, "rationale": "low precision"}]

    res = await anomaly.check_pipeline("p1", fetch_scores=scores, fetch_suggestions=sugg, post_annotation=post)
    check("anomaly check fetches suggestions and posts a Grafana annotation (tags: anomaly, pipeline)",
          res and posted and "anomaly" in posted[0]["tags"] and "p1" in posted[0]["tags"]
          and "top_k_after_rerank" in posted[0]["text"], posted[0]["text"] if posted else None)

    async def ok_scores(_): return stable + [0.80]
    posted.clear()
    check("no anomaly -> no annotation", await anomaly.check_pipeline("p1", fetch_scores=ok_scores, fetch_suggestions=sugg, post_annotation=post) is None and not posted)


async def main():
    tracing.setup_tracing("neuroflow-test", exporter=EXPORTER, simple=True)
    await test_traces()
    await test_metrics()
    await test_endpoint_and_infra()
    await test_anomaly()
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
