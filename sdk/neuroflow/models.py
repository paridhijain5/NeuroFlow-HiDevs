"""Typed result objects returned by the SDK. Parsing is tolerant of minor API field-name variants."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return default


@dataclass(frozen=True)
class Document:
    """An ingested document."""

    id: str
    status: str
    filename: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        return cls(
            id=str(_first(data, "id", "document_id", "doc_id", default="")),
            status=str(_first(data, "status", default="unknown")).lower(),
            filename=_first(data, "filename", "name", "source"),
            raw=data,
        )


@dataclass(frozen=True)
class QueryResult:
    """A completed RAG query."""

    run_id: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryResult:
        citations = _first(data, "citations", "sources", default=[]) or []
        return cls(
            run_id=str(_first(data, "run_id", "id", default="")),
            answer=str(_first(data, "answer", "response", default="")),
            citations=[c if isinstance(c, dict) else {"source": c} for c in citations],
            raw=data,
        )


@dataclass(frozen=True)
class EvaluationResult:
    """Evaluation scores for a query run."""

    run_id: str
    status: str
    scores: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def overall(self) -> float | None:
        value = self.scores.get("overall") or self.scores.get("overall_score")
        return float(value) if value is not None else None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationResult:
        scores = _first(data, "scores", "metrics", default={}) or {}
        return cls(
            run_id=str(_first(data, "run_id", default="")),
            status=str(_first(data, "status", default="complete")).lower(),
            scores={k: float(v) for k, v in scores.items() if isinstance(v, int | float)},
            raw=data,
        )
