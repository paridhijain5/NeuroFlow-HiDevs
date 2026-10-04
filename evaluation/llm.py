"""LLM / embedder protocols + default registry used by the metrics."""
from __future__ import annotations
from typing import Protocol


class LLM(Protocol):
    async def complete(self, prompt: str, *, temperature: float = 0.0) -> str: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


_llm: LLM | None = None
_embedder: Embedder | None = None


def configure(llm: LLM, embedder: Embedder) -> None:
    global _llm, _embedder
    _llm, _embedder = llm, embedder


def get_llm(llm: LLM | None = None) -> LLM:
    llm = llm or _llm
    if llm is None:
        raise RuntimeError("No LLM configured: call evaluation.llm.configure(...) or pass llm=")
    return llm


def get_embedder(e: Embedder | None = None) -> Embedder:
    e = e or _embedder
    if e is None:
        raise RuntimeError("No embedder configured: call evaluation.llm.configure(...) or pass embedder=")
    return e


class RouterLLM:
    """Adapter over your Task-N ModelRouter. ADAPT the call to match its real signature.
    Always routes with task_type="evaluation" (capable model, never a fine-tuned one)."""

    def __init__(self, router, task_type: str = "evaluation"):
        self.router, self.task_type = router, task_type

    async def complete(self, prompt: str, *, temperature: float = 0.0) -> str:
        from backend.monitoring import metrics
        metrics.record_llm_call("router", "unknown", self.task_type)      # ADAPT labels if the router exposes them
        return await self.router.complete(           # ADAPT
            task_type=self.task_type,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
