"""Unit tests for resolve_output_language_for_book precedence.

Precedence: explicit operator pick → book source language → global default.
No DB required.
"""

import pytest

from app.services.agent_models import resolve_output_language_for_book


def test_explicit_beats_book_and_global():
    """Explicit 'ru' wins even when book source is 'uz'."""
    assert resolve_output_language_for_book("ru", "uz", "uz") == "ru"


def test_book_source_beats_global_default():
    """None explicit → book source language wins over global default."""
    assert resolve_output_language_for_book(None, "ru", "uz") == "ru"


def test_global_default_when_no_explicit_no_book():
    """None explicit + no book source → global default."""
    assert resolve_output_language_for_book(None, None, "uz") == "uz"


def test_book_beats_global_when_both_differ():
    """Book source 'uz' beats global default 'ru'."""
    assert resolve_output_language_for_book(None, "uz", "ru") == "uz"


def test_bite_prove_fallback_order():
    """Bite-prove: if fallback order were (explicit or global or book), the
    None+ru-book+uz-global case would return 'uz' (wrong). The correct
    implementation returns 'ru' (book wins over global)."""
    # Correct implementation: explicit or book_source_language or global_default
    result = resolve_output_language_for_book(None, "ru", "uz")
    assert result == "ru", (
        "book source language must beat global default — "
        "got %r, expected 'ru'" % result
    )
