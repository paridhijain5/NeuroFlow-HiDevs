"""Every quality-sprint improvement is a configurable parameter (env var), not a hardcoded change."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class QualityParams:
    # chunking
    chunk_size: int = 512                 # try 256 / 512 / 768
    chunk_overlap: int = 64
    sentence_aware_chunking: bool = True
    parent_child_enabled: bool = False
    parent_chunk_tokens: int = 512
    child_chunk_tokens: int = 128
    # retrieval
    hnsw_ef_search: int = 100             # SET hnsw.ef_search at query time
    dense_k: int = 20
    sparse_k: int = 15
    rrf_k: int = 60
    dense_weight: float = 0.5             # 0.6 for technical domains
    sparse_weight: float = 0.5
    top_k_after_rerank: int = 8
    # prompt
    prompt_variant: str = "A"
    # latency
    embedding_cache_enabled: bool = True
    query_cache_enabled: bool = False
    query_cache_ttl_seconds: int = 1800   # 30 minutes
    parallel_retrieval: bool = True

    @classmethod
    def from_env(cls) -> QualityParams:
        d = cls()
        return cls(
            chunk_size=int(os.getenv("CHUNK_SIZE", d.chunk_size)),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP", d.chunk_overlap)),
            sentence_aware_chunking=_b("SENTENCE_AWARE_CHUNKING", d.sentence_aware_chunking),
            parent_child_enabled=_b("PARENT_CHILD_ENABLED", d.parent_child_enabled),
            parent_chunk_tokens=int(os.getenv("PARENT_CHUNK_TOKENS", d.parent_chunk_tokens)),
            child_chunk_tokens=int(os.getenv("CHILD_CHUNK_TOKENS", d.child_chunk_tokens)),
            hnsw_ef_search=int(os.getenv("HNSW_EF_SEARCH", d.hnsw_ef_search)),
            dense_k=int(os.getenv("DENSE_K", d.dense_k)),
            sparse_k=int(os.getenv("SPARSE_K", d.sparse_k)),
            rrf_k=int(os.getenv("RRF_K", d.rrf_k)),
            dense_weight=float(os.getenv("DENSE_WEIGHT", d.dense_weight)),
            sparse_weight=float(os.getenv("SPARSE_WEIGHT", d.sparse_weight)),
            top_k_after_rerank=int(os.getenv("TOP_K_AFTER_RERANK", d.top_k_after_rerank)),
            prompt_variant=os.getenv("PROMPT_VARIANT", d.prompt_variant),
            embedding_cache_enabled=_b("EMBEDDING_CACHE_ENABLED", d.embedding_cache_enabled),
            query_cache_enabled=_b("QUERY_CACHE_ENABLED", d.query_cache_enabled),
            query_cache_ttl_seconds=int(os.getenv("QUERY_CACHE_TTL_SECONDS", d.query_cache_ttl_seconds)),
            parallel_retrieval=_b("PARALLEL_RETRIEVAL", d.parallel_retrieval),
        )

    def fingerprint(self) -> str:
        """Stable string of params, used to invalidate caches when config changes."""
        return "|".join(f"{k}={v}" for k, v in sorted(asdict(self).items()))
