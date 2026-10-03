from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

import openai
from openai import AsyncOpenAI

from .base import BaseLLMProvider, ChatMessage, GenerationResult, retry_rate_limited

log = logging.getLogger(__name__)

# USD per million tokens
PRICES = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}
CONTEXT_WINDOWS = {"gpt-4o": 128_000, "gpt-4o-mini": 128_000}
EMBED_BATCH_SIZE = 100


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
        embedding_model: str = "text-embedding-3-small",
        client: AsyncOpenAI | None = None,
        max_retries: int = 3,
    ):
        self.model = model
        self.embedding_model = embedding_model
        self.max_retries = max_retries
        # SDK-level retries off: our own retry loop handles 429s visibly.
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url or None, max_retries=0
        )
        self._sleep = asyncio.sleep  # replaceable in tests

    # ---- pricing / limits -------------------------------------------------
    @property
    def cost_per_input_token(self) -> float:
        return PRICES.get(self.model, {"input": 0.0})["input"] / 1_000_000

    @property
    def cost_per_output_token(self) -> float:
        return PRICES.get(self.model, {"output": 0.0})["output"] / 1_000_000

    @property
    def context_window(self) -> int:
        return CONTEXT_WINDOWS.get(self.model, 128_000)

    # ---- helpers ----------------------------------------------------------
    async def _call(self, fn):
        return await retry_rate_limited(
            fn, openai.RateLimitError, max_retries=self.max_retries, sleep=self._sleep
        )

    @staticmethod
    def _to_openai(messages: list[ChatMessage]) -> list[dict]:
        # str or OpenAI-style content parts (text / image_url) pass straight through
        return [{"role": m.role, "content": m.content} for m in messages]

    # ---- interface --------------------------------------------------------
    async def complete(self, messages: list[ChatMessage], **kwargs) -> GenerationResult:
        start = time.perf_counter()
        resp = await self._call(
            lambda: self._client.chat.completions.create(
                model=self.model, messages=self._to_openai(messages), **kwargs
            )
        )
        choice = resp.choices[0]
        in_t = resp.usage.prompt_tokens if resp.usage else 0
        out_t = resp.usage.completion_tokens if resp.usage else 0
        return GenerationResult(
            content=choice.message.content or "",
            model=self.model,
            input_tokens=in_t,
            output_tokens=out_t,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=self.compute_cost(in_t, out_t),
            finish_reason=choice.finish_reason or "stop",
        )

    async def stream_events(
        self, messages: list[ChatMessage], **kwargs
    ) -> AsyncGenerator[str | GenerationResult, None]:
        start = time.perf_counter()
        stream = await self._call(
            lambda: self._client.chat.completions.create(
                model=self.model,
                messages=self._to_openai(messages),
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
        )
        parts: list[str] = []
        in_t = out_t = 0
        finish = "stop"
        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]
                if choice.delta and choice.delta.content:
                    parts.append(choice.delta.content)
                    yield choice.delta.content
                if choice.finish_reason:
                    finish = choice.finish_reason
            if getattr(chunk, "usage", None):
                in_t, out_t = chunk.usage.prompt_tokens, chunk.usage.completion_tokens
        yield GenerationResult(
            content="".join(parts),
            model=self.model,
            input_tokens=in_t,
            output_tokens=out_t,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=self.compute_cost(in_t, out_t),
            finish_reason=finish,
        )

    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncGenerator[str, None]:
        async for item in self.stream_events(messages, **kwargs):
            if isinstance(item, str):
                yield item

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            resp = await self._call(
                lambda b=batch: self._client.embeddings.create(model=self.embedding_model, input=b)
            )
            vectors.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return vectors
