"""Tests for the blank EXTRACT_PROVIDER → default "gemini" validator (extract-2)."""

import pytest
from app.config import Settings


def test_blank_extract_provider_falls_back_to_default():
    """Empty string should map to the default "gemini"."""
    s = Settings(_env_file=None, extract_provider="")
    assert s.extract_provider == "gemini"


def test_whitespace_extract_provider_falls_back_to_default():
    """Whitespace-only string should map to the default "gemini"."""
    s = Settings(_env_file=None, extract_provider="   ")
    assert s.extract_provider == "gemini"


def test_real_extract_provider_passes_through():
    """A real value like "claude" should not be altered."""
    s = Settings(_env_file=None, extract_provider="claude")
    assert s.extract_provider == "claude"
