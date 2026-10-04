"""Unit tests: Pydantic validation rejects bad pipeline configs.

ADAPT: assumed model PipelineConfig with chunk_size, chunk_overlap, top_k.
"""
import pytest
from pydantic import ValidationError

from backend.config.pipeline_config import PipelineConfig  # ADAPT

VALID = {"chunk_size": 512, "chunk_overlap": 64, "top_k": 5}


def test_valid_config_accepted() -> None:
    cfg = PipelineConfig(**VALID)
    assert cfg.chunk_size == 512 and cfg.top_k == 5


def test_negative_chunk_size_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(**{**VALID, "chunk_size": -1})


def test_zero_top_k_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(**{**VALID, "top_k": 0})


def test_overlap_not_smaller_than_chunk_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(**{**VALID, "chunk_size": 100, "chunk_overlap": 100})


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(**{**VALID, "chunk_size": "not-a-number"})


def test_negative_overlap_rejected() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(**{**VALID, "chunk_overlap": -5})
