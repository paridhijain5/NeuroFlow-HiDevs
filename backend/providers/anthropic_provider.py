from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator

import anthropic
from anthropic import AsyncAnthropic

from .base import BaseLLMProvider, ChatMessage, GenerationResult, retry_rate_limited

log = logging.getLogger(__name__)

# USD per million tokens. Verify against Anthropic's pricing page before relying on them.
PRICES = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5-5": {"input": 3.00, "output": 15.00},
}
CONTEXT_WINDOWS = {"claude-haiku-4-5-20251001": 200_000, "claude-sonnet-5-5": 200_000}
DEFAULT_MAX_TOKENS = 1024  # required by the Anthropic API


def _convert_part(part: dict) -> dict:
    """OpenAI-style content part -> Anthropic content block."""
    if part.get("type") == "image_url":
        url = part["image_url"]["url"] if isinstance(part["image_url"], dict) else part["image_url"]
        if url.startswith("data:"):
            header, data = url.split(",", 1)
            media_type = header[5:].split(";")[0]
            return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        return {"type": "image", "source": {"type": "url", "url": url}}
    return part  # text blocks and native Anthropic blocks pass through


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        api_key: str | None = None,
        client: AsyncAnthropic | None = None,
        max_retries: int = 3,
    ):
        self.model = model
        self.max_retries = max_retries
        self._client = client or AsyncAnthropic(api_key=api_key, max_retries=0)
        self._sleep = asyncio.sleep

    @property
    def cost_per_input_token(self) -> float:
        return PRICES.get(self.model, {"input": 0.0})["input"] / 1_000_000

    @property
    def cost_per_output_token(self) -> float:
        return PRICES.get(self.model, {"output": 0.0})["output"] / 1_000_000

    @property
    def context_window(self) -> int:
        return CONTEXT_WINDOWS.get(self.model, 200_000)

    async def _call(self, fn):
        return await retry_rate_limited(
            fn, anthropic.RateLimitError, max_retries=self.max_retries, sleep=self._sleep
        )

    @staticmethod
    def _split(messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
        """System messages are top-level in Anthropic's API, not in the messages list."""
        system_parts: list[str] = []
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content if isinstance(m.content, str) else
                                    " ".join(p.get("text", "") for p in m.content))
                continue
            content = m.content if isinstance(m.content, str) else [_convert_part(p) for p in m.content]
            out.append({"role": m.role, "content": content})
        return ("\n\n".join(system_parts) or None), out

    def _request(self, messages: list[ChatMessage], kwargs: dict) -> dict:
        system, msgs = self._split(messages)
        req = {"model": self.model, "messages": msgs,
               "max_tokens": kwargs.pop("max_tokens", DEFAULT_MAX_TOKENS), **kwargs}
        if system:
            req["system"] = system
        return req

    async def complete(self, messages: list[ChatMessage], **kwargs) -> GenerationResult:
        start = time.perf_counter()
        req = self._request(messages, dict(kwargs))
        resp = await self._call(lambda: self._client.messages.create(**req))
        text = "".join(b.text for b in resp.content if b.type == "text")
        in_t, out_t = resp.usage.input_tokens, resp.usage.output_tokens
        return GenerationResult(
            content=text,
            model=self.model,
            input_tokens=in_t,
            output_tokens=out_t,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=self.compute_cost(in_t, out_t),
            finish_reason=resp.stop_reason or "end_turn",
        )

    async def stream_events(
        self, messages: list[ChatMessage], **kwargs
    ) -> AsyncGenerator[str | GenerationResult, None]:
        start = time.perf_counter()
        req = self._request(messages, dict(kwargs))
        stream = await self._call(lambda: self._client.messages.create(stream=True, **req))
        parts: list[str] = []
        in_t = out_t = 0
        finish = "end_turn"
        async for event in stream:
            if event.type == "message_start":
                in_t = event.message.usage.input_tokens
            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                parts.append(event.delta.text)
                yield event.delta.text
            elif event.type == "message_delta":
                out_t = event.usage.output_tokens
                finish = event.delta.stop_reason or finish
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
        raise NotImplementedError(
            "Anthropic has no embeddings API; route task_type='embedding' to an embedding provider."
        )
