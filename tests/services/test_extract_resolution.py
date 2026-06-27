"""Tests for _resolve_extract — the pipeline's extract-role provider/model resolver.

The function now takes a `ld` DB-default object (a LaunchDefaults row or any
SimpleNamespace with .extract_provider / .extract_model) instead of reading
settings.  Jobs are stamped at launch so the null-path is defensive, not primary.
"""
from types import SimpleNamespace

import pytest

from app.services.pipeline import _resolve_extract

# A fake LaunchDefaults row representing the seeded DB defaults.
_LD = SimpleNamespace(extract_provider="gemini", extract_model="gemini-2.5-flash")


def test_resolve_extract_explicit_override():
    """An explicit job provider+model wins over the DB default."""
    assert _resolve_extract("claude", "claude-opus-4-7", _LD) == ("claude", "claude-opus-4-7")


def test_resolve_extract_falls_back_to_db_default():
    """NULL job columns fall through to the DB default (the defensive null-path)."""
    assert _resolve_extract(None, None, _LD) == ("gemini", "gemini-2.5-flash")


def test_resolve_extract_partial_override_provider_only():
    """Explicit provider + NULL model: provider wins, model falls back to DB default."""
    assert _resolve_extract("gemini", None, _LD) == ("gemini", "gemini-2.5-flash")


def test_resolve_extract_different_db_default():
    """The DB default can be anything; the fallback reads from ld, not settings."""
    ld2 = SimpleNamespace(extract_provider="claude", extract_model="claude-sonnet-4-6")
    assert _resolve_extract(None, None, ld2) == ("claude", "claude-sonnet-4-6")
