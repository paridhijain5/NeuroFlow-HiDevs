"""Custom Prometheus metrics. Every metric is updated WHERE the operation happens (generator, circuit breaker,
judge, ingestion, backpressure ...) through the small helpers at the bottom - never from a monitoring-only loop,
except the gauges that mirror shared Redis state (see refresh_from_redis)."""
from __future__ import annotations

import os
from collections import defaultdict, deque

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# ---------------------------------------------------------------- Counters
queries_total = Counter('neuroflow_queries_total', 'Total queries', ['pipeline_id', 'status'])
ingestion_docs_total = Counter('neuroflow_ingestion_docs_total', 'Documents ingested', ['source_type'])
llm_calls_total = Counter('neuroflow_llm_calls_total', 'LLM API calls', ['provider', 'model', 'task_type'])
circuit_breaker_trips = Counter('neuroflow_circuit_breaker_trips_total', 'Circuit breaker openings', ['provider'])

# ---------------------------------------------------------------- Histograms
retrieval_latency = Histogram('neuroflow_retrieval_latency_seconds', 'Retrieval latency', ['strategy'],
                              buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5])
generation_latency = Histogram('neuroflow_generation_latency_seconds', 'Generation latency', ['model'],
                               buckets=[0.5, 1, 2, 5, 10, 30])
llm_cost = Histogram('neuroflow_llm_cost_usd', 'LLM call cost in USD', ['model'],
                     buckets=[0.0001, 0.001, 0.01, 0.1, 1.0])

# ---------------------------------------------------------------- Gauges
eval_faithfulness = Gauge('neuroflow_eval_faithfulness', 'Rolling avg faithfulness', ['pipeline_id'])
eval_overall = Gauge('neuroflow_eval_overall', 'Rolling avg overall score', ['pipeline_id'])
queue_depth = Gauge('neuroflow_queue_depth', 'Ingestion queue depth')
active_circuit_breakers_open = Gauge('neuroflow_circuit_breakers_open', 'Number of open circuit breakers')

# ---- extras needed by the dashboards (task text only lists faithfulness/overall gauges, but the Quality Monitor
#      asks for "each evaluation metric per pipeline" and the System Overview for breaker status "per provider")
eval_answer_relevance = Gauge('neuroflow_eval_answer_relevance', 'Rolling avg answer relevance', ['pipeline_id'])
eval_context_precision = Gauge('neuroflow_eval_context_precision', 'Rolling avg context precision', ['pipeline_id'])
eval_context_recall = Gauge('neuroflow_eval_context_recall', 'Rolling avg context recall', ['pipeline_id'])
circuit_breaker_state = Gauge('neuroflow_circuit_breaker_state',
                              'Circuit state per provider (0 closed, 0.5 half-open, 1 open)', ['provider'])

# ---------------------------------------------------------------- pricing (ADAPT: USD per 1M tokens, input/output)
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
}
DEFAULT_PRICE = (float(os.getenv("PRICE_INPUT_PER_1M", "0.15")), float(os.getenv("PRICE_OUTPUT_PER_1M", "0.60")))
ROLLING_WINDOW = 100


def llm_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = next((v for k, v in MODEL_PRICES.items() if model.startswith(k)), DEFAULT_PRICE)
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


# ---------------------------------------------------------------- helpers called at the point of the operation
def _pid(pipeline_id) -> str:
    return "unknown" if pipeline_id is None else str(pipeline_id)


def record_query(pipeline_id, status: str) -> None:
    queries_total.labels(_pid(pipeline_id), status).inc()


def record_ingested_doc(source_type: str) -> None:
    ingestion_docs_total.labels(source_type).inc()


def observe_retrieval(strategy: str, seconds: float) -> None:
    retrieval_latency.labels(strategy).observe(seconds)


def record_llm_call(provider: str, model: str, task_type: str, *, latency_s: float | None = None,
                    input_tokens: int | None = None, output_tokens: int | None = None) -> float | None:
    """Counts the call; observes latency / cost only when they are known. Returns the cost (USD) if computed."""
    llm_calls_total.labels(provider, model, task_type).inc()
    if latency_s is not None:
        generation_latency.labels(model).observe(latency_s)
    if input_tokens is None or output_tokens is None:
        return None
    cost = llm_cost_usd(model, input_tokens, output_tokens)
    llm_cost.labels(model).observe(cost)
    return cost


_rolling: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=ROLLING_WINDOW))
_EVAL_GAUGES = {"faithfulness": eval_faithfulness, "answer_relevance": eval_answer_relevance,
                "context_precision": eval_context_precision, "context_recall": eval_context_recall,
                "overall": eval_overall}


def record_evaluation(pipeline_id, *, faithfulness: float, answer_relevance: float, context_precision: float,
                      context_recall: float, overall: float) -> None:
    """Rolling average over the last 100 evaluations per pipeline (per process). The dashboards use
    avg_over_time(...[24h]) on top of these gauges for the 24h views."""
    pid = _pid(pipeline_id)
    for name, value in (("faithfulness", faithfulness), ("answer_relevance", answer_relevance),
                        ("context_precision", context_precision), ("context_recall", context_recall),
                        ("overall", overall)):
        window = _rolling[(pid, name)]
        window.append(value)
        _EVAL_GAUGES[name].labels(pid).set(sum(window) / len(window))


_breaker_states: dict[str, str] = {}
_STATE_VALUE = {"closed": 0.0, "half_open": 0.5, "open": 1.0}


def set_breaker_state(provider: str, state: str) -> None:
    _breaker_states[provider] = state
    circuit_breaker_state.labels(provider).set(_STATE_VALUE.get(state, 0.0))
    active_circuit_breakers_open.set(sum(1 for s in _breaker_states.values() if s == "open"))


def record_circuit_trip(provider: str) -> None:
    circuit_breaker_trips.labels(provider).inc()
    set_breaker_state(provider, "open")


async def refresh_from_redis(redis, queue_key: str = "queue:ingest") -> None:
    """Gauges that mirror state shared through Redis (other workers also change it)."""
    queue_depth.set(int(await redis.llen(queue_key)))
    async for key in redis.scan_iter(match="circuit:*:state", count=100):
        set_breaker_state(key.split(":")[1], await redis.get(key) or "closed")


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
