"""Tests for per-language Notion container crawl and available-language detection.

Mirrors the fake-client style from test_notion_fetch.py:
  c.get_child_pages.side_effect = lambda pid: children_by_parent.get(pid, [])
  c.get_block_children.side_effect = lambda pid: blocks_by_page.get(pid, [])

A PDF block accepted by _first_pdf_block is:
  {"type": "file", "file": {"name": "x.pdf", "file": {"url": "…"}}}
or {"type": "pdf", "pdf": {"file": {"url": "…"}}}.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from app.services import notion_fetch as nf


# ---------------------------------------------------------------------------
# Helpers (same style as test_notion_fetch.py)
# ---------------------------------------------------------------------------

def _client(children_by_parent: dict, blocks_by_page: dict | None = None):
    c = MagicMock()
    c.get_child_pages.side_effect = lambda pid: children_by_parent.get(pid, [])
    c.get_block_children.side_effect = lambda pid: (blocks_by_page or {}).get(pid, [])
    return c


def _pdf_block(url: str = "http://cdn.example.com/file.pdf") -> dict:
    """A file-type PDF block accepted by _first_pdf_block."""
    return {"type": "file", "file": {"name": "textbook.pdf", "file": {"url": url}}}


# ---------------------------------------------------------------------------
# Shared grade tree: 9 - sinf (Uzbek) + 9 - класс (Russian); no English.
# Under sinf: Algebra (has PDF), Geografiya (has PDF), Fizika (has PDF).
# Under класс: Алгебра (has PDF), Физика (NO pdf — tests has_textbook=False).
# ---------------------------------------------------------------------------

GRADE_ID = "g9"
UZ_CONTAINER_ID = "uz-sinf"
RU_CONTAINER_ID = "ru-klass"

_CHILDREN_BASE = {
    GRADE_ID: [
        {"id": UZ_CONTAINER_ID, "title": "9 - sinf"},
        {"id": RU_CONTAINER_ID, "title": "9 - класс"},
    ],
    UZ_CONTAINER_ID: [
        {"id": "uz-alg", "title": "Algebra"},
        {"id": "uz-geo", "title": "Geografiya"},
        {"id": "uz-fiz", "title": "Fizika"},
    ],
    RU_CONTAINER_ID: [
        {"id": "ru-alg", "title": "Алгебра"},
        {"id": "ru-fiz", "title": "Физика"},
    ],
}

_BLOCKS_BASE = {
    "uz-alg": [_pdf_block("http://cdn/uz-algebra.pdf")],
    "uz-geo": [_pdf_block("http://cdn/uz-geo.pdf")],
    "uz-fiz": [_pdf_block("http://cdn/uz-fizika.pdf")],
    "ru-alg": [_pdf_block("http://cdn/ru-algebra.pdf")],
    "ru-fiz": [{"type": "paragraph"}],  # no PDF → has_textbook=False
}


# ---------------------------------------------------------------------------
# list_subjects_for_language — Russian
# ---------------------------------------------------------------------------

class TestListSubjectsForLanguageRu:
    def _make_client(self):
        return _client(_CHILDREN_BASE, _BLOCKS_BASE)

    def test_returns_subjects_under_klass_container(self):
        c = self._make_client()
        subs = nf.list_subjects_for_language(c, GRADE_ID, "ru")
        titles = [s["notion_title"] for s in subs]
        assert "Алгебра" in titles
        assert "Физика" in titles

    def test_maps_algebra_to_math_algebra(self):
        c = self._make_client()
        subs = nf.list_subjects_for_language(c, GRADE_ID, "ru")
        by_title = {s["notion_title"]: s for s in subs}
        assert by_title["Алгебра"]["app_subject"] == "math-algebra"

    def test_has_textbook_flag_set_correctly(self):
        c = self._make_client()
        subs = nf.list_subjects_for_language(c, GRADE_ID, "ru")
        by_title = {s["notion_title"]: s for s in subs}
        assert by_title["Алгебра"]["has_textbook"] is True
        assert by_title["Физика"]["has_textbook"] is False

    def test_returns_page_id(self):
        c = self._make_client()
        subs = nf.list_subjects_for_language(c, GRADE_ID, "ru")
        by_title = {s["notion_title"]: s for s in subs}
        assert by_title["Алгебра"]["page_id"] == "ru-alg"

    def test_returns_empty_when_no_klass_container(self):
        c = _client({"g9": [{"id": UZ_CONTAINER_ID, "title": "9 - sinf"}]}, {})
        assert nf.list_subjects_for_language(c, "g9", "ru") == []


# ---------------------------------------------------------------------------
# list_subjects — Uzbek backward-compat wrapper still works
# ---------------------------------------------------------------------------

class TestListSubjectsBackwardCompat:
    def test_uz_wrapper_still_returns_sinf_subjects(self):
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        subs = nf.list_subjects(c, GRADE_ID)
        titles = [s["notion_title"] for s in subs]
        assert "Algebra" in titles
        assert "Geografiya" in titles
        assert "Fizika" in titles

    def test_uz_wrapper_does_not_include_klass_subjects(self):
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        subs = nf.list_subjects(c, GRADE_ID)
        titles = [s["notion_title"] for s in subs]
        assert "Алгебра" not in titles

    def test_uz_wrapper_maps_subjects_correctly(self):
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        subs = nf.list_subjects(c, GRADE_ID)
        by_title = {s["notion_title"]: s for s in subs}
        assert by_title["Algebra"]["app_subject"] == "math-algebra"

    def test_uz_wrapper_returns_empty_when_no_sinf(self):
        c = _client({"g1": [{"id": RU_CONTAINER_ID, "title": "1 - класс"}]})
        assert nf.list_subjects(c, "g1") == []


# ---------------------------------------------------------------------------
# available_languages — uz+ru present, no English
# ---------------------------------------------------------------------------

class TestAvailableLanguagesNoEnglish:
    def _make_client(self):
        return _client(_CHILDREN_BASE, _BLOCKS_BASE)

    def test_math_algebra_has_uz_and_ru(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        assert "math-algebra" in result
        langs = result["math-algebra"]
        assert "uz" in langs
        assert "ru" in langs
        assert "en" not in langs

    def test_geografiya_uz_only(self):
        """Geografiya exists only under sinf (no RU keyword for geografiya in registry)."""
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        assert "geografiya" in result
        langs = result["geografiya"]
        assert "uz" in langs
        assert "ru" not in langs
        assert "en" not in langs

    def test_lang_entry_contains_page_id_and_has_textbook(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        uz_entry = result["math-algebra"]["uz"]
        assert uz_entry["page_id"] == "uz-alg"
        assert uz_entry["has_textbook"] is True
        ru_entry = result["math-algebra"]["ru"]
        assert ru_entry["page_id"] == "ru-alg"
        assert ru_entry["has_textbook"] is True

    def test_subject_with_no_textbook_excluded(self):
        """ru-fiz has no PDF → Физика (physics) should NOT appear under 'ru'."""
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        # physics may appear under 'uz' (Fizika has a PDF)
        if "physics" in result:
            assert "ru" not in result["physics"]

    def test_no_english_key_when_no_en_container(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        for langs in result.values():
            assert "en" not in langs


# ---------------------------------------------------------------------------
# available_languages — with English container added
# ---------------------------------------------------------------------------

class TestAvailableLanguagesWithEnglish:
    def _make_client(self):
        children = {
            **_CHILDREN_BASE,
            GRADE_ID: [
                {"id": UZ_CONTAINER_ID, "title": "9 - sinf"},
                {"id": RU_CONTAINER_ID, "title": "9 - класс"},
                {"id": "en-grade", "title": "9 - grade"},
            ],
            "en-grade": [
                {"id": "en-alg", "title": "Algebra"},
            ],
        }
        blocks = {
            **_BLOCKS_BASE,
            "en-alg": [_pdf_block("http://cdn/en-algebra.pdf")],
        }
        return _client(children, blocks)

    def test_math_algebra_has_uz_ru_and_en(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        assert "math-algebra" in result
        langs = result["math-algebra"]
        assert "uz" in langs
        assert "ru" in langs
        assert "en" in langs

    def test_en_entry_has_correct_page_id(self):
        c = self._make_client()
        result = nf.available_languages(c, GRADE_ID)
        assert result["math-algebra"]["en"]["page_id"] == "en-alg"
        assert result["math-algebra"]["en"]["has_textbook"] is True


# ---------------------------------------------------------------------------
# Bite-prove: monkeypatch _LANG_CONTAINER_RE["ru"] to a non-matching pattern
# → the ru entries must vanish, causing assertions about "ru" in langs to FAIL
# (verified by removing the monkeypatch and confirming the normal test passes).
# ---------------------------------------------------------------------------

class TestBiteProveRuRegex:
    def test_ru_entries_present_without_monkeypatch(self):
        """Baseline: math-algebra should have 'ru' with the real regex."""
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        result = nf.available_languages(c, GRADE_ID)
        assert "ru" in result.get("math-algebra", {})

    def test_ru_entries_vanish_when_regex_broken(self, monkeypatch):
        """If the ru container regex never matches, ru entries must disappear."""
        monkeypatch.setitem(nf._LANG_CONTAINER_RE, "ru", re.compile(r"NOMATCH_IMPOSSIBLE_XYZ"))
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        result = nf.available_languages(c, GRADE_ID)
        # With a non-matching regex, no subject should have 'ru'
        for app_subject, langs in result.items():
            assert "ru" not in langs, (
                f"Expected no 'ru' entries with broken regex, but {app_subject!r} has 'ru'"
            )

    def test_bite_prove_assertion_would_fail_without_fix(self, monkeypatch):
        """Meta-test: confirm that 'ru in langs' FAILS when regex is broken,
        so the bite-prove test above is not vacuous."""
        monkeypatch.setitem(nf._LANG_CONTAINER_RE, "ru", re.compile(r"NOMATCH_IMPOSSIBLE_XYZ"))
        c = _client(_CHILDREN_BASE, _BLOCKS_BASE)
        result = nf.available_languages(c, GRADE_ID)
        # This assertion should fail (ru is absent)
        math_langs = result.get("math-algebra", {})
        with pytest.raises(AssertionError):
            assert "ru" in math_langs, "ru should be absent — bite-prove working"
