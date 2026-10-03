from __future__ import annotations

import json
from dataclasses import dataclass, field, replace


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    content: str
    score: float = 0.0                      # final score of whichever stage produced this list
    metadata: dict = field(default_factory=dict)
    filename: str = ""
    strategies: list[str] = field(default_factory=list)   # which retrievers found it
    rrf_score: float | None = None
    rerank_score: float | None = None

    @classmethod
    def from_row(cls, row, strategy: str) -> "RetrievalResult":
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        return cls(chunk_id=str(row["id"]), document_id=str(row["document_id"]), content=row["content"],
                   score=float(row["score"] or 0.0), metadata=meta or {},
                   filename=row["filename"] or "", strategies=[strategy])

    def copy(self, **changes) -> "RetrievalResult":
        changes.setdefault("strategies", list(self.strategies))
        return replace(self, **changes)
