"""Regression tests for the Learning Sections schemas (PR-2) integrated from
the Toriqli branch: Memory Check + flashcard stable IDs.

CBP/DPE itself is covered in test_flow_v2_schemas.py; here we pin the new
Memory Check rules (flashcard_id required, 3 kinds, 0.60 threshold locked) and
the flashcard stable-ID rules (required + unique).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.flashcards import Flashcard, FlashcardsPack
from app.schemas.memory_check import MemoryCheckItem, MemoryCheckPack


def _item(**over) -> dict:
    base = dict(
        flashcard_id="card_1",
        prompt="Which rule divides a fraction by a whole number?",
        kind="multiple_choice",
        options=["multiply denominator", "multiply numerator"],
        correct_index=0,
    )
    base.update(over)
    return base


# ── Memory Check ──────────────────────────────────────────────────────


def test_memory_check_valid() -> None:
    pack = MemoryCheckPack(items=[MemoryCheckItem(**_item())])
    assert pack.pass_threshold == 0.60
    assert pack.items[0].flashcard_id == "card_1"


def test_memory_check_item_requires_flashcard_id() -> None:
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_item(flashcard_id=""))


def test_memory_check_item_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        MemoryCheckItem(**_item(kind="essay"))


def test_memory_check_threshold_locked_at_sixty() -> None:
    # The pack threshold is locked at 0.60 — any other value is rejected.
    with pytest.raises(ValidationError):
        MemoryCheckPack(items=[MemoryCheckItem(**_item())], pass_threshold=0.5)


# ── Flashcard stable IDs ──────────────────────────────────────────────


def test_flashcard_requires_id() -> None:
    with pytest.raises(ValidationError):
        Flashcard(id="", front="f", back="b")


def test_flashcards_pack_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        FlashcardsPack(
            cards=[
                Flashcard(id="card_1", front="a", back="b"),
                Flashcard(id="card_1", front="c", back="d"),
            ]
        )


def test_flashcards_pack_unique_ids_ok() -> None:
    pack = FlashcardsPack(
        cards=[
            Flashcard(id="card_1", front="a", back="b"),
            Flashcard(id="card_2", front="c", back="d"),
        ]
    )
    assert [c.id for c in pack.cards] == ["card_1", "card_2"]
