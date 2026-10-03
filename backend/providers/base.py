"""Provider-agnostic interface, shared types, and the rate-limit retry helper."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Awaitable, Callable, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str | list  # str for text, list of parts for multi-modal


@dataclass
class GenerationResult:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    finish_reason: str


class BaseLLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def complete(self, messages: list[ChatMessage], **kwargs) -> GenerationResult: ...

    @abstractmethod
    async def stream(self, messages: list[ChatMessage], **kwargs) -> AsyncGenerator[str, None]: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def cost_per_input_token(self) -> float: ...  # USD per single token

    @property
    @abstractmethod
    def cost_per_output_token(self) -> float: ...

    @property
    @abstractmethod
    def context_window(self) -> int: ...

    async def stream_events(
        self, messages: list[ChatMessage], **kwargs
    ) -> AsyncGenerator[str | GenerationResult, None]:
        """Yield text tokens, then one final GenerationResult carrying usage/cost.

        Providers override this; the default only yields tokens (no usage info).
        """
        async for token in self.stream(messages, **kwargs):
            yield token

    def compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.cost_per_input_token + output_tokens * self.cost_per_output_token


def retry_after_seconds(exc: Exception) -> float | None:
    """Read the Retry-After header from an SDK rate-limit exception, if present."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def retry_rate_limited(
    call: Callable[[], Awaitable[T]],
    rate_limit_exc: type[Exception],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Retry on rate-limit errors up to `max_retries` times.

    Wait = max(server's retry_after, exponential backoff base_delay * 2**attempt).
    """
    for attempt in range(max_retries + 1):
        try:
            return await call()
        except rate_limit_exc as exc:
            if attempt == max_retries:
                raise
            delay = max(retry_after_seconds(exc) or 0.0, base_delay * 2**attempt)
            log.warning("Rate limited; retry %d/%d in %.1fs", attempt + 1, max_retries, delay)
            await sleep(delay)
    raise RuntimeError("unreachable")
