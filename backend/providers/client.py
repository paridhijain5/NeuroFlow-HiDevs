"""NeuroFlowClient: the single entry point the rest of the app uses for LLM calls."""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from opentelemetry import trace

from config import settings

from .anthropic_provider import AnthropicProvider
from .base import BaseLLMProvider, ChatMessage, GenerationResult
from .fallback import FallbackChain
from .openai_provider import OpenAIProvider
from .router import ModelConfig, ModelRouter, RoutingCriteria

log = logging.getLogger(__name__)
tracer = trace.get_tracer("neuroflow.providers")


def estimate_tokens(messages: list[ChatMessage]) -> int:
    """Rough estimate (~4 chars/token) used only for routing/cost filtering."""
    chars = 0
    for m in messages:
        if isinstance(m.content, str):
            chars += len(m.content)
        else:
            chars += sum(len(p.get("text", "")) for p in m.content if isinstance(p, dict))
    return max(1, chars // 4)


class NeuroFlowClient:
    _instance: "NeuroFlowClient | None" = None

    def __init__(self, redis, router: ModelRouter | None = None,
                 providers: dict[str, BaseLLMProvider] | None = None):
        self.redis = redis
        self.router = router or ModelRouter(redis)
        self._providers: dict[str, BaseLLMProvider] = providers or {}

    @classmethod
    def get(cls, redis=None) -> "NeuroFlowClient":
        """Process-wide singleton. Pass `redis` on first call (e.g. from app lifespan)."""
        if cls._instance is None:
            if redis is None:
                raise RuntimeError("NeuroFlowClient.get() needs a redis client on first use")
            cls._instance = cls(redis)
        return cls._instance

    # ---- provider construction -------------------------------------------
    def _provider_for(self, cfg: ModelConfig) -> BaseLLMProvider:
        if cfg.name not in self._providers:
            if cfg.provider == "openai":
                key = settings.openai_api_key or settings.llm_api_key
                if cfg.kind == "embedding":
                    p = OpenAIProvider(api_key=key, base_url=settings.openai_base_url,
                                       embedding_model=cfg.name)
                else:
                    p = OpenAIProvider(model=cfg.name, api_key=key, base_url=settings.openai_base_url)
            elif cfg.provider == "anthropic":
                p = AnthropicProvider(model=cfg.name, api_key=settings.anthropic_api_key)
            else:
                raise ValueError(f"Unknown provider '{cfg.provider}' for model {cfg.name}")
            self._providers[cfg.name] = p
        return self._providers[cfg.name]

    # ---- metrics -----------------------------------------------------------
    async def _record(self, model: str, cost_usd: float) -> None:
        await self.redis.incr(f"metrics:model:{model}:calls")
        await self.redis.incrbyfloat(f"metrics:model:{model}:cost_usd", cost_usd)

    @staticmethod
    def _annotate(span, r: GenerationResult) -> None:
        span.set_attribute("model", r.model)
        span.set_attribute("input_tokens", r.input_tokens)
        span.set_attribute("output_tokens", r.output_tokens)
        span.set_attribute("cost_usd", r.cost_usd)
        span.set_attribute("latency_ms", r.latency_ms)

    # ---- public API --------------------------------------------------------
    async def chat(self, messages: list[ChatMessage],
                   routing_criteria: RoutingCriteria | None = None, **kwargs) -> GenerationResult:
        criteria = routing_criteria or RoutingCriteria()
        cfg = await self.router.route(criteria, estimated_input_tokens=estimate_tokens(messages))
        provider = self._provider_for(cfg)
        with tracer.start_as_current_span("llm.chat") as span:
            result = await provider.complete(messages, **kwargs)
            self._annotate(span, result)
        await self._record(result.model, result.cost_usd)
        return result

    async def chat_stream(self, messages: list[ChatMessage],
                          routing_criteria: RoutingCriteria | None = None,
                          **kwargs) -> AsyncGenerator[str, None]:
        criteria = routing_criteria or RoutingCriteria()
        cfg = await self.router.route(criteria, estimated_input_tokens=estimate_tokens(messages))
        provider = self._provider_for(cfg)
        with tracer.start_as_current_span("llm.chat_stream") as span:
            async for item in provider.stream_events(messages, **kwargs):
                if isinstance(item, str):
                    yield item
                else:
                    self._annotate(span, item)
                    await self._record(item.model, item.cost_usd)

    async def chat_with_fallback(self, messages: list[ChatMessage], model_names: list[str],
                                 **kwargs) -> GenerationResult:
        """Try the named models in order (e.g. ['gpt-4o-mini','claude-haiku-4-5-20251001','gpt-4o'])."""
        registry = {m.name: m for m in await self.router.load_models()}
        unknown = [n for n in model_names if n not in registry]
        if unknown:
            raise ValueError(f"Models not in registry: {unknown}")
        chain = FallbackChain([self._provider_for(registry[n]) for n in model_names])
        with tracer.start_as_current_span("llm.chat_fallback") as span:
            result = await chain.complete(messages, **kwargs)
            self._annotate(span, result)
        await self._record(result.model, result.cost_usd)
        return result

    async def embed(self, texts: list[str]) -> list[list[float]]:
        cfg = await self.router.route(RoutingCriteria(task_type="embedding"),
                                      estimated_input_tokens=sum(len(t) for t in texts) // 4 or 1)
        provider = self._provider_for(cfg)
        with tracer.start_as_current_span("llm.embed") as span:
            vectors = await provider.embed(texts)
            est_tokens = sum(len(t) for t in texts) // 4
            cost = est_tokens * cfg.cost_input_per_m / 1_000_000  # estimate: API reports no usage here
            span.set_attribute("model", cfg.name)
            span.set_attribute("input_tokens", est_tokens)
            span.set_attribute("output_tokens", 0)
            span.set_attribute("cost_usd", cost)
        await self._record(cfg.name, cost)
        return vectors
