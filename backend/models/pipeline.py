"""Pydantic schema for a named, versioned pipeline config. Unknown keys are rejected everywhere."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestionConfig(_Strict):
    chunking_strategy: str = Field("recursive", min_length=1)
    chunk_size_tokens: int = Field(512, gt=0)
    chunk_overlap_tokens: int = Field(64, ge=0)
    extractors_enabled: list[str] = Field(default_factory=lambda: ["pdf", "docx"])

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self):
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("chunk_overlap_tokens must be smaller than chunk_size_tokens")
        return self


class RetrievalConfig(_Strict):
    dense_k: int = Field(20, ge=0, le=500)
    sparse_k: int = Field(10, ge=0, le=500)
    reranker: str | None = None                      # e.g. "cross-encoder"
    top_k_after_rerank: int = Field(5, ge=1, le=100)
    query_expansion: bool = False
    metadata_filters_enabled: bool = False

    @model_validator(mode="after")
    def _top_k_reachable(self):
        if self.dense_k + self.sparse_k < 1:
            raise ValueError("dense_k + sparse_k must be at least 1")
        if self.top_k_after_rerank > self.dense_k + self.sparse_k:
            raise ValueError("top_k_after_rerank cannot exceed dense_k + sparse_k")
        return self


class ModelRouting(_Strict):
    task_type: str = "rag_generation"
    max_cost_per_call: float = Field(0.05, gt=0)


class GenerationConfig(_Strict):
    model_routing: ModelRouting = Field(default_factory=ModelRouting)
    max_context_tokens: int = Field(6000, gt=0)
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    system_prompt_variant: str = "precise"


class EvaluationConfig(_Strict):
    auto_evaluate: bool = True
    training_threshold: float = Field(0.8, ge=0.0, le=1.0)


class PipelineConfig(_Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]{1,62}$")
    description: str = ""
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
