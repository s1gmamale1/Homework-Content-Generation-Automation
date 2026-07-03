"""Task 6 — API language-payload tests.

(a) BookOut.model_validate carries source_language from the Book ORM model.
(b) GET /api/v1/notion/grades/{id}/available-languages returns the per-subject
    language dict via the notion_fetch.available_languages crawl.
(c) GET /jobs/batches carries output_language — confirmed via _rollup_payload
    (already serialized upstream in #62; do NOT add a second serializer).

Bite-proof: removing source_language from BookOut breaks assertion (a).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.schemas.book import BookOut
from app.api.v1.batch import _rollup_payload


# ─── (a) BookOut serializes source_language ──────────────────────────────────

class TestBookOutSourceLanguage:
    """BookOut.model_validate picks up source_language from a Book-like object."""

    def _make_book(self, lang: str):
        return SimpleNamespace(
            id=uuid4(),
            subject="math-algebra",
            grade="8",
            original_filename="algebra.pdf",
            status="toc_ready",
            source_language=lang,
            error_message=None,
            gemini_file_expires_at=None,
            file_size_bytes=None,
            created_at=None,
            toc=None,
        )

    def test_source_language_ru_serializes(self):
        book = self._make_book("ru")
        out = BookOut.model_validate(book)
        assert out.source_language == "ru", (
            f"Expected source_language='ru', got {out.source_language!r}"
        )

    def test_source_language_uz_serializes(self):
        book = self._make_book("uz")
        out = BookOut.model_validate(book)
        assert out.source_language == "uz"

    def test_source_language_en_serializes(self):
        book = self._make_book("en")
        out = BookOut.model_validate(book)
        assert out.source_language == "en"

    def test_source_language_present_in_json(self):
        book = self._make_book("ru")
        out = BookOut.model_validate(book)
        d = out.model_dump()
        assert "source_language" in d, "source_language key missing from BookOut.model_dump()"
        assert d["source_language"] == "ru"

    # Bite-prove note: if source_language is removed from BookOut, model_validate
    # will still succeed (source_language simply won't appear), but out.source_language
    # would raise AttributeError — confirming the test is not vacuous.


# ─── (b) GET /notion/grades/{id}/available-languages endpoint ─────────────────

class TestAvailableLanguagesEndpoint:
    """The notion router endpoint calls notion_fetch.available_languages and
    returns its result.  Mirrors test_notion_router.py's patch-based style."""

    @pytest.fixture(autouse=True)
    def _auth_override(self):
        from main import app
        from app.auth import get_current_user
        app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_available_languages_returns_per_subject_dict(self):
        from main import app
        c = TestClient(app)
        fake_result = {
            "math-algebra": {
                "uz": {"page_id": "uz-alg", "has_textbook": True},
                "ru": {"page_id": "ru-alg", "has_textbook": True},
            }
        }
        with patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages",
                   return_value=fake_result):
            r = c.get("/api/v1/notion/grades/g9/available-languages")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "math-algebra" in data
        assert "uz" in data["math-algebra"]
        assert "ru" in data["math-algebra"]
        assert data["math-algebra"]["uz"]["page_id"] == "uz-alg"

    def test_available_languages_502_on_notion_error(self):
        from main import app
        c = TestClient(app)
        with patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages",
                   side_effect=RuntimeError("Notion down")):
            r = c.get("/api/v1/notion/grades/g9/available-languages")
        assert r.status_code == 502

    def test_available_languages_empty_when_no_containers(self):
        from main import app
        c = TestClient(app)
        with patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages",
                   return_value={}):
            r = c.get("/api/v1/notion/grades/g9/available-languages")
        assert r.status_code == 200
        assert r.json() == {}


# ─── (c) _rollup_payload carries output_language (already upstream in #62) ───

def _fake_batch_for_rollup(output_language: str = "en"):
    return SimpleNamespace(
        id=uuid4(), book_id=uuid4(), subject="math-algebra", grade="8",
        output_language=output_language,
        provider="gemini", model="gemini-2.5-flash", transport="api",
        extract_transport="inherit", judge_transport="inherit",
        solver_transport="inherit",
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
        created_at=datetime(2026, 6, 25, tzinfo=timezone.utc),
        paused_at=None, paused_reason=None,
        session_limit_strategy="inherit",
    )


def test_rollup_payload_carries_output_language():
    """_rollup_payload (already live in batch.py) emits output_language.
    This assertion guards against regression — no new serializer is added here."""
    payload = _rollup_payload(_fake_batch_for_rollup("en"), {"done": 5}, "alg.pdf")
    assert "output_language" in payload, (
        "output_language key missing from _rollup_payload — upstream #62 may have been reverted"
    )
    assert payload["output_language"] == "en"


def test_rollup_payload_output_language_uz():
    payload = _rollup_payload(_fake_batch_for_rollup("uz"), {"done": 2})
    assert payload["output_language"] == "uz"
