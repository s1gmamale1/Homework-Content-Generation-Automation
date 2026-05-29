"""Phase 3 Learning Sections cross-output validation."""

from __future__ import annotations

import pytest

from app.schemas.flashcards import Flashcard, FlashcardsPack
from app.schemas.memory_check import MemoryCheckItem, MemoryCheckPack
from app.services.pipeline import _validate_memory_check_refs


def _flashcards() -> FlashcardsPack:
    return FlashcardsPack(
        cards=[
            Flashcard(id="card_1", front="Term", back="Definition"),
            Flashcard(id="card_2", front="Why?", back="Because..."),
        ]
    )


def test_memory_check_refs_match_flashcard_ids() -> None:
    memory_check = MemoryCheckPack(
        items=[
            MemoryCheckItem(
                flashcard_id="card_1",
                prompt="Choose the matching definition.",
                kind="multiple_choice",
                options=["Definition", "Wrong", "Wrong", "Wrong"],
                correct_index=0,
            ),
            MemoryCheckItem(
                flashcard_id="card_2",
                prompt="_____ explains the reason.",
                kind="fill_blank",
                explanation="Because...",
            ),
        ]
    )

    _validate_memory_check_refs(memory_check, _flashcards())


def test_memory_check_rejects_unknown_flashcard_id() -> None:
    memory_check = MemoryCheckPack(
        items=[
            MemoryCheckItem(
                flashcard_id="card_404",
                prompt="Choose the matching explanation.",
                kind="choose_correct_explanation",
                options=["Right", "Wrong", "Wrong", "Wrong"],
                correct_index=0,
            )
        ]
    )

    with pytest.raises(ValueError, match="unknown flashcard ids: card_404"):
        _validate_memory_check_refs(memory_check, _flashcards())


def test_memory_check_requires_structured_flashcards() -> None:
    memory_check = MemoryCheckPack(
        items=[
            MemoryCheckItem(
                flashcard_id="card_1",
                prompt="Choose the matching definition.",
                kind="multiple_choice",
                options=["Definition", "Wrong", "Wrong", "Wrong"],
                correct_index=0,
            )
        ]
    )

    with pytest.raises(ValueError, match="requires structured flashcards"):
        _validate_memory_check_refs(memory_check, None)
