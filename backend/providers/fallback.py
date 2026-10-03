"""FallbackChain: try providers in order until one succeeds."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from .base import BaseLLMProvider, ChatMessage, GenerationResult

log = logging.getLogger(__name__)


class AllProvidersFailed(Exception):
    def __init__(self, errors: list[tuple[str, Exception]]):
        self.errors = errors
        detail = "; ".join(f"{name}: {type(e).__name__}: {e}" for name, e in errors)
        super().__init__(f"All providers failed -> {detail}")


class FallbackChain:
    """e.g. FallbackChain([gpt-4o-mini, claude-haiku, gpt-4o]).

    Each provider already retries rate limits internally, so any exception that
    reaches the chain is treated as "this provider is unavailable": move on.
    """

    def __init__(self, providers: list[BaseLLMProvider]):
        if not providers:
            raise ValueError("FallbackChain needs at least one provider")
        self.providers = providers

    async def complete(self, messages: list[ChatMessage], **kwargs) -> GenerationResult:
        errors: list[tuple[str, Exception]] = []
        for p in self.providers:
            try:
                return await p.complete(messages, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Provider %s failed (%s); trying next", p.model, exc)
                errors.append((p.model, exc))
        raise AllProvidersFailed(errors)

    async def stream_events(
        self, messages: list[ChatMessage], **kwargs
    ) -> AsyncGenerator[str | GenerationResult, None]:
        """Falls back only if a provider fails before emitting its first token."""
        errors: list[tuple[str, Exception]] = []
        for p in self.providers:
            started = False
            try:
                async for item in p.stream_events(messages, **kwargs):
                    started = True
                    yield item
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if started:
                    raise  # mid-stream failure: switching would duplicate/garble output
                log.warning("Provider %s failed before streaming (%s); trying next", p.model, exc)
                errors.append((p.model, exc))
        raise AllProvidersFailed(errors)
