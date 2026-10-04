import pytest

from backend.ingestion.parent_child import (
    build_parent_child,
    count_tokens,
    expand_to_parents,
    pack_sentences,
)

TEXT = " ".join(f"Sentence number {i} talks about retrieval and ranking." for i in range(60))


def test_children_within_budget() -> None:
    _, children = build_parent_child(TEXT, parent_tokens=100, child_tokens=30)
    assert children and all(count_tokens(c.text) <= 30 for c in children)


def test_parents_within_budget() -> None:
    parents, _ = build_parent_child(TEXT, parent_tokens=100, child_tokens=30)
    assert all(count_tokens(p.text) <= 100 for p in parents)


def test_every_child_has_valid_parent() -> None:
    parents, children = build_parent_child(TEXT, parent_tokens=100, child_tokens=30)
    ids = {p.id for p in parents}
    assert all(c.parent_id in ids for c in children)


def test_expand_dedupes_and_keeps_order() -> None:
    parents, children = build_parent_child(TEXT, parent_tokens=100, child_tokens=30)
    cmap = {c.id: c for c in children}
    pmap = {p.id: p for p in parents}
    first_kids = [c.id for c in children if c.parent_id == parents[0].id]
    out = expand_to_parents([children[-1].id, *first_kids], cmap, pmap)
    assert out[0].id == children[-1].parent_id
    assert len({p.id for p in out}) == len(out)


def test_oversized_sentence_is_split() -> None:
    pieces = pack_sentences("word " * 50, 10)
    assert len(pieces) == 5 and all(count_tokens(p) <= 10 for p in pieces)


def test_child_larger_than_parent_rejected() -> None:
    with pytest.raises(ValueError):
        build_parent_child("Hello world.", parent_tokens=10, child_tokens=20)


def test_unknown_child_ignored() -> None:
    assert expand_to_parents(["nope"], {}, {}) == []
