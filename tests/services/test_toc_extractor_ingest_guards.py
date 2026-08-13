"""The ingest guards, wired through `toc_extractor.run`.

`tests/services/test_toc_ingest_audit.py` pins the audit's decisions in
isolation; this file pins that the extractor actually applies them — that a
repaired page range is what reaches `bulk_create`, that a blocking finding
routes the book to `toc_review` (entries still persisted, book NOT failed),
and that a scanned book is flagged without being rejected.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from app.services import toc_extractor


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        pass


_DEFAULT_LAUNCH_DEFAULTS = SimpleNamespace(
    extract_provider="gemini",
    extract_model="gemini-3.5-flash-lite",
    toc_transport="api",
)


def _scanned_pdf(tmp_path: Path, n_pages: int = 30) -> Path:
    """Image-only book: real pages, zero text layer (what the 12 live failures hit)."""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=200, height=200)
    out = tmp_path / "scanned.pdf"
    with out.open("wb") as fh:
        writer.write(fh)
    return out


def _entry(title="Lesson", ps=None, pe=None):
    return SimpleNamespace(section_title=title, page_start=ps, page_end=pe)


def _patch(monkeypatch, entries, *, validation=None, validation_enabled=True):
    """Wire the extractor onto fakes; returns a captured-calls dict."""
    seen: dict = {"statuses": [], "validations": [], "bulk": [], "events": [], "ready_at": []}

    async def fake_set_status(session, book_id, status, error_message=None):
        seen["statuses"].append((status, error_message))

    async def fake_set_toc_validation(session, book_id, verdict, detail):
        seen["validations"].append((verdict, detail))

    async def fake_set_toc_ready_at(session, book_id):
        seen["ready_at"].append(book_id)

    async def fake_bulk_create(session, book_id, rows):
        # Snapshot the page bounds AS PERSISTED — the repair has to have landed
        # on the entry objects before this call, not afterwards.
        seen["bulk"].append([(r.section_title, r.page_start, r.page_end) for r in rows])
        return [
            SimpleNamespace(
                id=uuid4(),
                chapter_number=None,
                chapter_title=None,
                section_number=None,
                section_title=r.section_title,
                page_start=r.page_start,
                page_end=r.page_end,
                order_index=i,
            )
            for i, r in enumerate(rows)
        ]

    async def fake_delete_for_book(session, book_id):
        return 0

    async def fake_publish(rid, ev, data):
        seen["events"].append((ev, data))

    async def fake_close(rid):
        pass

    async def fake_get_launch_defaults(session):
        return _DEFAULT_LAUNCH_DEFAULTS

    async def fake_extract_toc(**kw):
        return SimpleNamespace(entries=entries)

    async def fake_validate_toc(**kw):
        assert validation is not None, "validate_toc must not be called when disabled"
        return validation

    monkeypatch.setattr(toc_extractor, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(toc_extractor.books_repo, "set_status", fake_set_status)
    monkeypatch.setattr(toc_extractor.books_repo, "set_toc_validation", fake_set_toc_validation)
    monkeypatch.setattr(toc_extractor.books_repo, "set_toc_ready_at", fake_set_toc_ready_at)
    monkeypatch.setattr(toc_extractor.toc_repo, "bulk_create", fake_bulk_create)
    monkeypatch.setattr(toc_extractor.toc_repo, "delete_for_book", fake_delete_for_book)
    monkeypatch.setattr(toc_extractor.events_bus, "publish", fake_publish)
    monkeypatch.setattr(toc_extractor.events_bus, "close", fake_close)
    monkeypatch.setattr(toc_extractor.launch_defaults_repo, "get", fake_get_launch_defaults)
    monkeypatch.setattr(toc_extractor.agent, "extract_toc", fake_extract_toc)
    monkeypatch.setattr(toc_extractor.agent, "validate_toc", fake_validate_toc)
    monkeypatch.setattr(toc_extractor.settings, "toc_validation_enabled", validation_enabled)
    return seen


_VERIFIED = SimpleNamespace(status="verified", confidence="high", issues=[], detail="")


# ── inverted page ranges ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_off_by_one_range_is_repaired_before_persist(tmp_path, monkeypatch):
    """`35-34` must never reach `toc_entries`. All three measured failures
    (`cannot scope page range 35-34 / 50-49 / 72-71`) had this shape."""
    entries = [_entry("Dars 12", 35, 34), _entry("Dars 13", 36, 38)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["bulk"] == [[("Dars 12", 35, 35), ("Dars 13", 36, 38)]], (
        "the repaired range must be what gets persisted"
    )


@pytest.mark.asyncio
async def test_repaired_book_still_reaches_toc_ready(tmp_path, monkeypatch):
    """A repair is not a defect the operator has to arbitrate — it is recorded
    and the book proceeds."""
    seen = _patch(monkeypatch, [_entry("Dars", 35, 34)], validation=_VERIFIED)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["statuses"][-1][0] == "toc_ready"
    assert len(seen["ready_at"]) == 1
    verdict, detail = seen["validations"][-1]
    assert verdict == "verified"
    assert "repaired" in detail and "35-34" in detail, "the repair must be on the audit trail"


@pytest.mark.asyncio
async def test_unrepairable_inversion_routes_to_review(tmp_path, monkeypatch):
    entries = [_entry("Bob 3", 120, 44)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["statuses"][-1][0] == "toc_review"
    assert seen["ready_at"] == [], "a book under review must not be stamped ready"
    verdict, detail = seen["validations"][-1]
    assert verdict == "mismatch"
    assert "120-44" in detail
    # ...and the rows are still there for the operator to fix by hand.
    assert seen["bulk"] == [[("Bob 3", 120, 44)]]
    assert "failed" not in [s for s, _ in seen["statuses"]]


@pytest.mark.asyncio
async def test_blocking_finding_survives_a_disabled_vision_gate(tmp_path, monkeypatch):
    """`toc_validation_enabled` gates a PAID vision call. The free arithmetic
    check on rows we just wrote is not part of that bargain — and the review
    SSE must not assume a validator result exists."""
    seen = _patch(monkeypatch, [_entry("Bob 3", 120, 44)], validation_enabled=False)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["statuses"][-1][0] == "toc_review"
    verdict, detail = seen["validations"][-1]
    assert verdict == "mismatch" and "120-44" in detail
    review = next(data for ev, data in seen["events"] if ev == "toc_review")
    assert review["validation"]["verdict"] == "mismatch"
    assert any("120-44" in issue for issue in review["validation"]["issues"])


@pytest.mark.asyncio
async def test_audit_detail_merges_with_the_validator_detail(tmp_path, monkeypatch):
    mismatch = SimpleNamespace(
        status="mismatch", confidence="high", issues=["Lesson 5 not on page 42"],
        detail="Lesson 5 not on page 42",
    )
    seen = _patch(monkeypatch, [_entry("Dars", 35, 34)], validation=mismatch)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    _verdict, detail = seen["validations"][-1]
    assert "repaired" in detail, "the deterministic finding must survive the merge"
    assert "Lesson 5 not on page 42" in detail, "the validator's prose must survive too"


# ── NULL page ranges ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_null_page_ranges_pass_through_untouched(tmp_path, monkeypatch):
    """413 of 9,634 live rows carry a NULL range and are legitimate — the guard
    must not invent bounds for them or hold the book up."""
    entries = [_entry("I BOB", None, None), _entry("Javoblar", 200, None)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["bulk"] == [[("I BOB", None, None), ("Javoblar", 200, None)]]
    assert seen["statuses"][-1][0] == "toc_ready"
    assert seen["validations"][-1] == ("verified", None), "nothing to report"


# ── sparse text layer ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanned_book_is_flagged_but_not_rejected(tmp_path, monkeypatch):
    """A scanned book with page ranges extracts fine via the vision path
    (worklogs 0070/0072/0094) — record the finding, do not drop support."""
    pdf = _scanned_pdf(tmp_path)
    entries = [_entry("Dars 1", 5, 7), _entry("Dars 2", 8, 10)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), pdf, "math-algebra")

    assert seen["statuses"][-1][0] == "toc_ready", "a scanned book must not be blocked"
    assert "failed" not in [s for s, _ in seen["statuses"]]
    verdict, detail = seen["validations"][-1]
    assert verdict == "verified"
    assert "scanned/no text layer" in detail, "the whole book is flagged once, on the book"
    assert "chars/page" in detail


@pytest.mark.asyncio
async def test_scanned_book_records_the_finding_with_the_vision_gate_off(tmp_path, monkeypatch):
    """With no validator verdict to attach to, the finding still lands on the
    book — verdict stays NULL ('not validated'), detail carries what we saw."""
    pdf = _scanned_pdf(tmp_path)
    seen = _patch(monkeypatch, [_entry("Dars 1", 5, 7)], validation_enabled=False)

    await toc_extractor.run(uuid4(), pdf, "math-algebra")

    assert seen["statuses"][-1][0] == "toc_ready"
    verdict, detail = seen["validations"][-1]
    assert verdict is None, "no verdict may be invented when the validator did not run"
    assert "scanned/no text layer" in detail


@pytest.mark.asyncio
async def test_scanned_book_with_a_page_less_lesson_routes_to_review(tmp_path, monkeypatch):
    """The exact live conjunction — `sparse text layer (1 chars/page) — likely
    scanned and no page range` — which 12 lessons across 5 hosts each paid a
    claim and a book fetch to rediscover. Flagged once, on the book."""
    pdf = _scanned_pdf(tmp_path)
    entries = [_entry("Dars 1", 5, 7), _entry("Dars 2", None, None)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), pdf, "math-algebra")

    assert seen["statuses"][-1][0] == "toc_review"
    verdict, detail = seen["validations"][-1]
    assert verdict == "mismatch"
    assert "no page range" in detail
    # Surfaced, not rejected: rows persisted, book not failed, one click to accept.
    assert seen["bulk"] == [[("Dars 1", 5, 7), ("Dars 2", None, None)]]
    assert "failed" not in [s for s, _ in seen["statuses"]]


@pytest.mark.asyncio
async def test_scanned_book_page_less_end_matter_does_not_hold_the_book(tmp_path, monkeypatch):
    pdf = _scanned_pdf(tmp_path)
    entries = [_entry("Dars 1", 5, 7), _entry("Javoblar", None, None)]
    seen = _patch(monkeypatch, entries, validation=_VERIFIED)

    await toc_extractor.run(uuid4(), pdf, "math-algebra")

    assert seen["statuses"][-1][0] == "toc_ready"


@pytest.mark.asyncio
async def test_audit_failure_never_fails_an_ingestion(tmp_path, monkeypatch):
    """Belt-and-braces: the guards are advisory infrastructure. If the audit
    itself blows up, the book must still ingest."""
    seen = _patch(monkeypatch, [_entry("Dars", 5, 7)], validation=_VERIFIED)

    def _boom(*a, **kw):
        raise RuntimeError("pypdf exploded")

    monkeypatch.setattr(toc_extractor.toc_ingest_audit, "probe_text_layer", _boom)

    await toc_extractor.run(uuid4(), tmp_path / "missing.pdf", "math-algebra")

    assert seen["statuses"][-1][0] == "toc_ready"
    assert "failed" not in [s for s, _ in seen["statuses"]]
