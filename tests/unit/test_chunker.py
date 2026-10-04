"""Unit tests for chunking strategies.

ADAPT: change the import / call signature below if your chunker differs.
Assumed API: chunk_text(text, strategy, chunk_size, overlap) -> list[str]
"""
import pytest

from backend.ingestion.chunker import chunk_text  # ADAPT

SAMPLE = (
    "Neural networks learn representations. They are trained with gradient descent. "
    "Retrieval augments generation with context.\n\n"
    "Hybrid search combines dense and sparse signals. Fusion merges ranked lists. "
    "Rerankers refine the top results."
)
STRATEGIES = ["fixed", "sentence", "paragraph", "recursive"]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_returns_non_empty_list_of_strings(strategy: str) -> None:
    chunks = chunk_text(SAMPLE, strategy=strategy, chunk_size=120, overlap=10)
    assert chunks and all(isinstance(c, str) and c.strip() for c in chunks)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_content_lost(strategy: str) -> None:
    chunks = chunk_text(SAMPLE, strategy=strategy, chunk_size=120, overlap=10)
    joined = " ".join(chunks)
    for word in ("gradient", "Fusion", "Rerankers", "Retrieval"):
        assert word in joined


def test_fixed_chunks_respect_size() -> None:
    chunks = chunk_text(SAMPLE, strategy="fixed", chunk_size=100, overlap=0)
    assert all(len(c) <= 100 for c in chunks)


def test_overlap_creates_shared_text() -> None:
    chunks = chunk_text("abcdefghij" * 20, strategy="fixed", chunk_size=50, overlap=10)
    assert len(chunks) > 1
    assert chunks[0][-10:] == chunks[1][:10]


def test_empty_text_gives_no_chunks() -> None:
    assert chunk_text("", strategy="fixed", chunk_size=50, overlap=5) == []


def test_short_text_single_chunk() -> None:
    assert len(chunk_text("Short text.", strategy="fixed", chunk_size=500, overlap=50)) == 1


def test_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        chunk_text(SAMPLE, strategy="does-not-exist", chunk_size=100, overlap=10)
