from uuid import uuid4

from app.schemas.book import BookOut


def test_bookout_subject_variant_ozbekiston():
    b = BookOut(
        id=uuid4(), subject="history", grade="8",
        original_filename="8-sinf O'zbekiston tarixi.pdf", status="toc_ready",
    )
    assert b.subject_variant == "ozbekiston"
    assert b.model_dump()["subject_variant"] == "ozbekiston"


def test_bookout_subject_variant_jahon():
    b = BookOut(
        id=uuid4(), subject="history", grade="8",
        original_filename="8-sinf Jahon tarixi.pdf", status="toc_ready",
    )
    assert b.subject_variant == "jahon"


def test_bookout_subject_variant_none_for_non_history():
    b = BookOut(
        id=uuid4(), subject="math-algebra", grade="8",
        original_filename="8-sinf Algebra.pdf", status="toc_ready",
    )
    assert b.subject_variant is None
    assert "subject_variant" in b.model_dump()
