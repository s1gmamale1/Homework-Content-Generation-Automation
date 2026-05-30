from app.schemas.flashcards import Flashcard, FlashcardsPack
from app.services.pipeline import _synth_md_for_structured


def test_flashcards_synth_shows_type_difficulty_and_optionals() -> None:
    pack = FlashcardsPack(cards=[
        Flashcard(
            id="card_1", front="Mitoxondriya", back="Hujayra energiya markazi",
            type="definition", difficulty="easy",
            explanation="ATP shu yerda ishlab chiqariladi.",
            example="Mushak hujayralarida ko'p bo'ladi.",
            misconception="Yadro bilan adashtirmang.",
        ),
    ])
    md = _synth_md_for_structured("flashcards", pack)
    assert "definition" in md and "easy" in md
    assert "ATP shu yerda" in md
    assert "Mushak hujayralarida" in md
    assert "adashtirmang" in md
