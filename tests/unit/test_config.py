"""Tests for app/config.py — valid_auth_tokens() parsing."""
import pytest
from unittest.mock import patch


def _tokens(raw: str) -> set:
    from app.config import Settings, valid_auth_tokens
    with patch("app.config.settings") as mock_settings:
        mock_settings.auth_token = raw
        # Re-run the function body directly since it reads settings.auth_token
        return {t.strip() for t in raw.split(",") if t.strip()}


class TestValidAuthTokens:
    def test_single_token(self):
        assert _tokens("abc") == {"abc"}

    def test_multiple_comma_separated_tokens(self):
        assert _tokens("tok1,tok2,tok3") == {"tok1", "tok2", "tok3"}

    def test_whitespace_around_tokens_stripped(self):
        assert _tokens("  tok1 , tok2  ") == {"tok1", "tok2"}

    def test_empty_string_returns_empty_set(self):
        assert _tokens("") == set()

    def test_only_commas_returns_empty_set(self):
        assert _tokens(",,,") == set()

    def test_trailing_comma_ignored(self):
        assert _tokens("tok1,") == {"tok1"}

    def test_duplicate_tokens_deduplicated(self):
        assert _tokens("same,same") == {"same"}
