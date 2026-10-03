from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)

REGISTRY_KEY = "router:models"
LONG_CONTEXT_THRESHOLD = 100_000


class NoModelAvailable(Exception):
    """No registered model satisfies the hard constraints."""


@dataclass
class RoutingCriteria:
    task_type: str = "rag_generation"  # rag_generation | evaluation | embedding | classification
    max_cost_per_call: float | None = None
    require_vision: bool = False
    require_long_context: bool = False  # > 100k tokens
    latency_budget_ms: int | None = None
    prefer_fine_tuned: bool = False


@dataclass
class ModelConfig:
    name: str
    provider: str  # "openai" | "anthropic"
    kind: str = "chat"  # "chat" | "embedding"
    supports_vision: bool = False
    context_window: int = 128_000
    cost_input_per_m: float = 0.0  # USD per million tokens
    cost_output_per_m: float = 0.0
    capable: bool = False  # strong enough to act as an evaluation judge
    avg_latency_ms: int = 2000
    is_fine_tuned: bool = False
    fine_tuned_for: str | None = None  # task_type this fine-tune serves

    def estimated_cost(self, in_tokens: int, out_tokens: int) -> float:
        return (in_tokens * self.cost_input_per_m + out_tokens * self.cost_output_per_m) / 1_000_000


DEFAULT_MODELS = [
    ModelConfig("gpt-4o", "openai", supports_vision=True, context_window=128_000,
                cost_input_per_m=2.50, cost_output_per_m=10.00, capable=True, avg_latency_ms=2500),
    ModelConfig("gpt-4o-mini", "openai", supports_vision=True, context_window=128_000,
                cost_input_per_m=0.15, cost_output_per_m=0.60, capable=False, avg_latency_ms=1200),
    ModelConfig("claude-haiku-4-5-20251001", "anthropic", supports_vision=True, context_window=200_000,
                cost_input_per_m=1.00, cost_output_per_m=5.00, capable=True, avg_latency_ms=1500),
    ModelConfig("claude-sonnet-5-5", "anthropic", supports_vision=True, context_window=200_000,
                cost_input_per_m=3.00, cost_output_per_m=15.00, capable=True, avg_latency_ms=2500),
    ModelConfig("text-embedding-3-small", "openai", kind="embedding", context_window=8191,
                cost_input_per_m=0.02),
]


class ModelRouter:
    """Selects a model from the registry stored in Redis key `router:models`."""

    def __init__(self, redis, registry_key: str = REGISTRY_KEY):
        self.redis = redis
        self.registry_key = registry_key

    async def seed_defaults(self) -> None:
        """Write the default registry if none exists yet (SET NX)."""
        payload = json.dumps([asdict(m) for m in DEFAULT_MODELS])
        await self.redis.set(self.registry_key, payload, nx=True)

    async def load_models(self) -> list[ModelConfig]:
        raw = await self.redis.get(self.registry_key)
        if not raw:
            await self.seed_defaults()
            raw = await self.redis.get(self.registry_key)
        if isinstance(raw, bytes):
            raw = raw.decode()
        return [ModelConfig(**d) for d in json.loads(raw)]

    async def register_model(self, config: ModelConfig) -> None:
        """Add/replace a model (e.g. when a fine-tuning job completes)."""
        models = [m for m in await self.load_models() if m.name != config.name]
        models.append(config)
        await self.redis.set(self.registry_key, json.dumps([asdict(m) for m in models]))

    async def route(
        self,
        criteria: RoutingCriteria,
        estimated_input_tokens: int = 1000,
        estimated_output_tokens: int = 500,
    ) -> ModelConfig:
        models = await self.load_models()
        is_embedding = criteria.task_type == "embedding"
        out_tokens = 0 if is_embedding else estimated_output_tokens

        def cost(m: ModelConfig) -> float:
            return m.estimated_cost(estimated_input_tokens, out_tokens)

        pool = [m for m in models if m.kind == ("embedding" if is_embedding else "chat")]

        # Hard constraints (rules 1, 2, 4, 5)
        if criteria.require_vision:
            pool = [m for m in pool if m.supports_vision]
        if criteria.require_long_context:
            pool = [m for m in pool if m.context_window > LONG_CONTEXT_THRESHOLD]
        if criteria.task_type == "evaluation":
            pool = [m for m in pool if m.capable and not m.is_fine_tuned]
        if criteria.max_cost_per_call is not None:
            pool = [m for m in pool if cost(m) <= criteria.max_cost_per_call]
        if not pool:
            raise NoModelAvailable(f"No model satisfies {criteria}")

        # Rule 3: a registered fine-tune for this task type wins, if allowed
        if criteria.prefer_fine_tuned and criteria.task_type != "evaluation":
            tuned = [m for m in pool if m.is_fine_tuned and m.fine_tuned_for == criteria.task_type]
            if tuned:
                return min(tuned, key=lambda m: (cost(m), m.name))

        # Default (rule 6): cheapest base model; fine-tunes are only used via rule 3
        pool = [m for m in pool if not m.is_fine_tuned]
        if not pool:
            raise NoModelAvailable(f"Only fine-tuned models available for {criteria}")

        # Latency budget is a soft preference: use it if any model fits
        if criteria.latency_budget_ms is not None:
            fast = [m for m in pool if m.avg_latency_ms <= criteria.latency_budget_ms]
            if fast:
                pool = fast
            else:
                log.warning("No model meets latency budget %dms; ignoring", criteria.latency_budget_ms)

        return min(pool, key=lambda m: (cost(m), m.name))
