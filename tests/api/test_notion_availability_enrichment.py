"""GET /api/v1/notion/grades/{id}/available-languages — system-state
enrichment (worklog 0144 task 4, prepare-status-redo).

The route wraps the (sync) Notion crawl `notion_fetch.available_languages`
and, AFTER it returns, batch-enriches every textbook candidate that's already
linked to a book row (`book_notion_sources`) with that book's system state:
`book_id`/`book_status`/`toc_validation`/`toc_total`/`toc_ready_at`/
`redo_blocked_by_jobs`. A part also gets a convenience rollup — `prepared:
true` + the same fields — when EXACTLY ONE of its candidates is linked.

Batch-load contract: however many subjects/languages/parts/candidates the
crawl returns, the route issues exactly ONE `links_for_sources` call, ONE
`books_repo.get_many`, ONE `toc_repo.count_by_book_ids`, ONE
`jobs_repo.count_by_book_ids` — never a per-candidate/per-part query. Every
repo call is patched here (mocked-session style, mirrors
test_from_notion.py's fully-mocked convention) so call counts are directly
assertable and no DB is needed.

Back-compat: existing keys (page_id, has_textbook, filename, rank, url, ...)
are never touched; enrichment only ADDS keys, and only on candidates/parts
that resolve to a linked book.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _auth_override():
    from main import app
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _candidate(page_id: str, block_id: str, filename: str = "book.pdf") -> dict:
    return {"page_id": page_id, "block_id": block_id, "filename": filename,
            "rank": 0, "url": f"https://notion.so/{block_id}"}


def _part(page_id: str, title: str, candidates: list[dict]) -> dict:
    return {"page_id": page_id, "title": title, "has_textbook": True, "candidates": candidates}


def _book(status: str, *, toc_validation=None, toc_ready_at=None):
    return SimpleNamespace(status=status, toc_validation=toc_validation, toc_ready_at=toc_ready_at)


def _patches(crawl_result: dict, *, links: dict, books: dict, toc_totals: dict, blocked: dict):
    return [
        patch("app.api.v1.notion.NotionClientWrapper"),
        patch("app.api.v1.notion.notion_fetch.available_languages", return_value=crawl_result),
        patch("app.api.v1.notion.notion_sources_repo.links_for_sources",
              AsyncMock(return_value=links)),
        patch("app.api.v1.notion.books_repo.get_many", AsyncMock(return_value=books)),
        patch("app.api.v1.notion.toc_repo.count_by_book_ids", AsyncMock(return_value=toc_totals)),
        patch("app.api.v1.notion.jobs_repo.count_by_book_ids", AsyncMock(return_value=blocked)),
    ]


def _get(client):
    return client.get("/api/v1/notion/grades/g9/available-languages")


class TestPreparedCandidateEnrichment:
    def test_prepared_part_gets_candidate_and_rollup_fields(self):
        from main import app
        client = TestClient(app)
        book_id = uuid4()
        ready_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
        crawl = {
            "math-algebra": {
                "uz": {
                    "page_id": "part1", "has_textbook": True,
                    "parts": [_part("part1", "Matematika 1-qism",
                                     [_candidate("part1", "block1")])],
                }
            }
        }
        ps = _patches(
            crawl,
            links={("part1", "block1"): book_id},
            books={book_id: _book("toc_ready", toc_validation="verified", toc_ready_at=ready_at)},
            toc_totals={book_id: 42},
            blocked={},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        assert r.status_code == 200, r.text
        data = r.json()
        part = data["math-algebra"]["uz"]["parts"][0]
        candidate = part["candidates"][0]

        for target in (candidate, part):
            assert target["book_id"] == str(book_id)
            assert target["book_status"] == "toc_ready"
            assert target["toc_validation"] == "verified"
            assert target["toc_total"] == 42
            assert target["toc_ready_at"] == "2026-07-10T12:00:00+00:00"
            assert target["redo_blocked_by_jobs"] == 0
        assert part["prepared"] is True
        # Existing keys untouched.
        assert candidate["filename"] == "book.pdf"
        assert part["title"] == "Matematika 1-qism"

    def test_unprepared_candidate_gets_no_enrichment_keys(self):
        from main import app
        client = TestClient(app)
        crawl = {
            "math-algebra": {
                "uz": {"page_id": "part1", "has_textbook": True,
                       "parts": [_part("part1", "Matematika", [_candidate("part1", "block1")])]}
            }
        }
        ps = _patches(crawl, links={}, books={}, toc_totals={}, blocked={})
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        assert r.status_code == 200
        part = r.json()["math-algebra"]["uz"]["parts"][0]
        candidate = part["candidates"][0]
        assert "book_id" not in candidate
        assert "book_id" not in part
        assert "prepared" not in part

    def test_mid_extract_status_surfaced_honestly(self):
        from main import app
        client = TestClient(app)
        book_id = uuid4()
        crawl = {"m": {"uz": {"page_id": "p1", "has_textbook": True,
                              "parts": [_part("p1", "T", [_candidate("p1", "b1")])]}}}
        ps = _patches(
            crawl, links={("p1", "b1"): book_id},
            books={book_id: _book("toc_extracting")},
            toc_totals={}, blocked={},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        part = r.json()["m"]["uz"]["parts"][0]
        assert part["book_status"] == "toc_extracting"
        assert part["toc_ready_at"] is None
        assert part["toc_total"] == 0  # no toc rows yet, default-0

    def test_toc_review_status_surfaces_validation_verdict(self):
        from main import app
        client = TestClient(app)
        book_id = uuid4()
        crawl = {"m": {"uz": {"page_id": "p1", "has_textbook": True,
                              "parts": [_part("p1", "T", [_candidate("p1", "b1")])]}}}
        ps = _patches(
            crawl, links={("p1", "b1"): book_id},
            books={book_id: _book("toc_review", toc_validation="mismatch")},
            toc_totals={book_id: 10}, blocked={},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        part = r.json()["m"]["uz"]["parts"][0]
        assert part["book_status"] == "toc_review"
        assert part["toc_validation"] == "mismatch"

    def test_failed_status_surfaced_honestly(self):
        from main import app
        client = TestClient(app)
        book_id = uuid4()
        crawl = {"m": {"uz": {"page_id": "p1", "has_textbook": True,
                              "parts": [_part("p1", "T", [_candidate("p1", "b1")])]}}}
        ps = _patches(
            crawl, links={("p1", "b1"): book_id},
            books={book_id: _book("failed")},
            toc_totals={}, blocked={},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        part = r.json()["m"]["uz"]["parts"][0]
        assert part["book_status"] == "failed"

    def test_blocked_jobs_count_surfaced(self):
        from main import app
        client = TestClient(app)
        book_id = uuid4()
        crawl = {"m": {"uz": {"page_id": "p1", "has_textbook": True,
                              "parts": [_part("p1", "T", [_candidate("p1", "b1")])]}}}
        ps = _patches(
            crawl, links={("p1", "b1"): book_id},
            books={book_id: _book("toc_ready")},
            toc_totals={book_id: 5}, blocked={book_id: 3},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        part = r.json()["m"]["uz"]["parts"][0]
        assert part["redo_blocked_by_jobs"] == 3

    def test_ambiguous_part_multiple_linked_candidates_skips_rollup(self):
        """Two candidates on the same part resolve to DIFFERENT books — the
        per-part `prepared` rollup only applies to the exactly-one case, so
        this part gets per-candidate detail but no part-level rollup."""
        from main import app
        client = TestClient(app)
        book_a, book_b = uuid4(), uuid4()
        crawl = {"m": {"uz": {"page_id": "p1", "has_textbook": True,
                              "parts": [_part("p1", "T",
                                               [_candidate("p1", "b1"),
                                                _candidate("p1", "b2")])]}}}
        ps = _patches(
            crawl, links={("p1", "b1"): book_a, ("p1", "b2"): book_b},
            books={book_a: _book("toc_ready"), book_b: _book("toc_ready")},
            toc_totals={}, blocked={},
        )
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5]:
            r = _get(client)
        part = r.json()["m"]["uz"]["parts"][0]
        assert "prepared" not in part
        assert "book_id" not in part
        cands = part["candidates"]
        assert cands[0]["book_id"] == str(book_a)
        assert cands[1]["book_id"] == str(book_b)


class TestBatchLoadContract:
    def test_multiple_parts_across_subjects_hit_each_repo_call_exactly_once(self):
        from main import app
        client = TestClient(app)
        book1, book2 = uuid4(), uuid4()
        crawl = {
            "math-algebra": {
                "uz": {"page_id": "p1", "has_textbook": True, "parts": [
                    _part("p1", "T1", [_candidate("p1", "b1")]),
                    _part("p2", "T2", [_candidate("p2", "b2")]),
                ]},
                "ru": {"page_id": "p3", "has_textbook": True, "parts": [
                    _part("p3", "T3", [_candidate("p3", "b3")]),
                ]},
            },
            "fizika": {
                "uz": {"page_id": "p4", "has_textbook": True, "parts": [
                    _part("p4", "T4", [_candidate("p4", "b4")]),
                ]},
            },
        }
        links_mock = AsyncMock(return_value={("p1", "b1"): book1, ("p2", "b2"): book2})
        get_many_mock = AsyncMock(return_value={
            book1: _book("toc_ready"), book2: _book("toc_review"),
        })
        toc_mock = AsyncMock(return_value={book1: 7})
        jobs_mock = AsyncMock(return_value={book2: 2})
        with patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages", return_value=crawl), \
             patch("app.api.v1.notion.notion_sources_repo.links_for_sources", links_mock), \
             patch("app.api.v1.notion.books_repo.get_many", get_many_mock), \
             patch("app.api.v1.notion.toc_repo.count_by_book_ids", toc_mock), \
             patch("app.api.v1.notion.jobs_repo.count_by_book_ids", jobs_mock):
            r = _get(client)
        assert r.status_code == 200, r.text
        links_mock.assert_awaited_once()
        get_many_mock.assert_awaited_once()
        toc_mock.assert_awaited_once()
        jobs_mock.assert_awaited_once()

        # The SAME single links_for_sources call must have carried every
        # candidate across every subject/language/part — not a subset.
        called_pairs = set(links_mock.await_args.args[1])
        assert called_pairs == {("p1", "b1"), ("p2", "b2"), ("p3", "b3"), ("p4", "b4")}

        data = r.json()
        assert data["math-algebra"]["uz"]["parts"][0]["book_status"] == "toc_ready"
        assert data["math-algebra"]["uz"]["parts"][1]["book_status"] == "toc_review"
        assert data["math-algebra"]["uz"]["parts"][1]["redo_blocked_by_jobs"] == 2
        # Unlinked parts (p3/p4) stay untouched.
        assert "book_id" not in data["math-algebra"]["ru"]["parts"][0]
        assert "book_id" not in data["fizika"]["uz"]["parts"][0]

    def test_no_candidates_at_all_skips_every_repo_call(self):
        """Existing back-compat behavior (test_books_language_payload.py):
        a crawl result with no textbook candidates must not touch the DB at
        all — links_for_sources's own empty-input short-circuit means the
        route shouldn't even need a real session for this common case."""
        from main import app
        client = TestClient(app)
        crawl = {"math-algebra": {"uz": {"page_id": "uz-alg", "has_textbook": True}}}
        links_mock = AsyncMock(return_value={})
        get_many_mock = AsyncMock(return_value={})
        with patch("app.api.v1.notion.NotionClientWrapper"), \
             patch("app.api.v1.notion.notion_fetch.available_languages", return_value=crawl), \
             patch("app.api.v1.notion.notion_sources_repo.links_for_sources", links_mock), \
             patch("app.api.v1.notion.books_repo.get_many", get_many_mock):
            r = _get(client)
        assert r.status_code == 200
        get_many_mock.assert_not_awaited()
