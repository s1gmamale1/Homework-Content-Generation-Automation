"""toc_extractor.run must NOT mark a book `toc_ready` when extraction yields 0
lessons (scanned/image-only or unparseable PDF) — it should take the existing
failure path (status=`failed` + error event) so the empty book is visible to the
operator instead of silently showing no lessons. (WISHLIST `toc-empty-ready`.)"""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import toc_extractor


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        pass


def _patch_common(monkeypatch):
    statuses: list[tuple[str, str | None]] = []
    bulk_calls: list = []
    events: list[str] = []

    async def fake_set_status(session, book_id, status, error_message=None):
        statuses.append((status, error_message))

    async def fake_bulk_create(session, book_id, entries):
        bulk_calls.append(entries)
        return list(entries)

    async def fake_delete_for_book(session, book_id):
        return 0

    async def fake_publish(rid, ev, data):
        events.append(ev)

    async def fake_close(rid):
        pass

    monkeypatch.setattr(toc_extractor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(toc_extractor.books_repo, "set_status", fake_set_status)
    monkeypatch.setattr(toc_extractor.toc_repo, "bulk_create", fake_bulk_create)
    monkeypatch.setattr(toc_extractor.toc_repo, "delete_for_book", fake_delete_for_book)
    monkeypatch.setattr(toc_extractor.events_bus, "publish", fake_publish)
    monkeypatch.setattr(toc_extractor.events_bus, "close", fake_close)
    return statuses, bulk_calls, events


@pytest.mark.asyncio
async def test_zero_entries_marks_failed_not_ready(monkeypatch):
    statuses, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_extract_toc(**kw):
        return SimpleNamespace(entries=[])  # the scanned-PDF / empty case

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", fake_extract_toc)

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    names = [s for s, _ in statuses]
    assert "toc_ready" not in names, "0-entry extraction must NOT be marked ready"
    assert "failed" in names, "0-entry extraction should fail loudly"
    assert bulk_calls == [], "must not persist an empty entry set"
    assert "error" in events
    # Refinement: the failure must be actionable — name the cause AND the remedy.
    failed_msg = next(msg for s, msg in statuses if s == "failed")
    assert "extract_toc_front_pages" in failed_msg and "extract_toc_back_pages" in failed_msg
    assert "re-extract" in failed_msg


@pytest.mark.asyncio
async def test_reextract_clears_before_insert(monkeypatch):
    """A re-extract must REPLACE the prior entries, not append. The extractor
    has no upsert and toc_entries has no unique constraint, so it must
    delete_for_book BEFORE bulk_create within the same session/transaction."""
    statuses, bulk_calls, events = _patch_common(monkeypatch)
    order: list[str] = []

    async def fake_delete_for_book(session, book_id):
        order.append("delete")
        return 0

    async def fake_bulk_create(session, book_id, entries):
        order.append("bulk_create")
        bulk_calls.append(entries)
        return list(entries)

    async def fake_extract_toc(**kw):
        return SimpleNamespace(entries=[SimpleNamespace(section_title="L1")])

    monkeypatch.setattr(toc_extractor.toc_repo, "delete_for_book", fake_delete_for_book)
    monkeypatch.setattr(toc_extractor.toc_repo, "bulk_create", fake_bulk_create)
    monkeypatch.setattr(toc_extractor.agent, "extract_toc", fake_extract_toc)
    monkeypatch.setattr(
        toc_extractor.TOCEntryOut, "model_validate",
        classmethod(lambda cls, r: SimpleNamespace(model_dump=lambda mode=None: {})),
    )

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    assert order == ["delete", "bulk_create"], (
        "delete_for_book must run immediately before bulk_create (clear-before-insert)")


@pytest.mark.asyncio
async def test_nonzero_entries_still_marks_ready(monkeypatch):
    statuses, bulk_calls, events = _patch_common(monkeypatch)

    async def fake_extract_toc(**kw):
        return SimpleNamespace(entries=[SimpleNamespace(section_title="L1")])

    monkeypatch.setattr(toc_extractor.agent, "extract_toc", fake_extract_toc)
    # bulk_create returns ORM-ish rows; TOCEntryOut.model_validate is called on them.
    monkeypatch.setattr(
        toc_extractor.TOCEntryOut, "model_validate",
        classmethod(lambda cls, r: SimpleNamespace(model_dump=lambda mode=None: {})),
    )

    await toc_extractor.run(uuid4(), Path("/nonexistent.pdf"), "math-algebra")

    names = [s for s, _ in statuses]
    assert "toc_ready" in names
    assert "failed" not in names
    assert len(bulk_calls) == 1
