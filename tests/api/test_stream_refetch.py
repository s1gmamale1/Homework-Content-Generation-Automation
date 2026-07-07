"""Oversized bus events arrive as ``__refetch__`` markers (NOTIFY caps at
~8KB); the SSE endpoints must rebuild the full data from the DB — through
the same serialization as their initial replay, so clients see one schema
regardless of payload size."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import app.api.v1.books as books_mod
import app.api.v1.jobs as jobs_mod


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.asyncio
async def test_refetch_job_event_rebuilds_phase_completed_from_db():
    jid = uuid4()
    phase = SimpleNamespace(
        phase_name="reading", phase_order=7, status="done",
        output_md="# full markdown " + "x" * 10_000,
        tokens_input=1000, tokens_output=2000,
    )
    job = SimpleNamespace(id=jid, phase_outputs=[phase])
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=job)):
        data = await jobs_mod._refetch_job_event(
            jid, "phase_completed",
            {"__refetch__": True, "phase_name": "reading", "phase_order": 7,
             "tokens_input": 1000, "tokens_output": 2000},
        )
    assert data == {
        "phase_name": "reading",
        "phase_order": 7,
        "output_md": phase.output_md,
        "tokens_input": 1000,
        "tokens_output": 2000,
    }


@pytest.mark.asyncio
async def test_refetch_job_event_rebuilds_error_from_db():
    jid = uuid4()
    job = SimpleNamespace(id=jid, error_message="boss-arena: " + "e" * 9000)
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get", AsyncMock(return_value=job)):
        data = await jobs_mod._refetch_job_event(
            jid, "error", {"__refetch__": True, "phase_name": "boss-arena"}
        )
    assert data["message"] == job.error_message
    assert data["phase_name"] == "boss-arena"     # inline hint preserved
    assert "__refetch__" not in data


@pytest.mark.asyncio
async def test_refetch_job_event_falls_back_to_hints_when_row_missing():
    jid = uuid4()
    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases", AsyncMock(return_value=None)):
        data = await jobs_mod._refetch_job_event(
            jid, "phase_completed", {"__refetch__": True, "phase_order": 7}
        )
    assert data == {"phase_order": 7}


@pytest.mark.asyncio
async def test_refetch_book_event_rebuilds_toc_ready_via_enriched_helper():
    bid = uuid4()
    book = SimpleNamespace(id=bid, toc_validation=None, toc_validation_detail=None)
    entry = MagicMock()
    entry.model_dump.return_value = {"section_title": "L1", "order_index": 0}
    with patch.object(books_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(books_mod.books_repo, "get_with_toc",
                      AsyncMock(return_value=book)) as get_with_toc, \
         patch.object(books_mod, "_enriched_toc_entries",
                      AsyncMock(return_value=[entry])) as enriched:
        data = await books_mod._refetch_book_event(
            bid, "toc_ready", {"__refetch__": True}
        )
    # MUST fetch via get_with_toc (selectinload) — a plain books_repo.get has no
    # toc_entries relationship loaded and MissingGreenlet's on the lazy access
    # inside _enriched_toc_entries under the async ORM.
    get_with_toc.assert_awaited_once()
    enriched.assert_awaited_once()   # MUST go through the shared helper
    assert data == {"entries": [{"section_title": "L1", "order_index": 0}]}


@pytest.mark.asyncio
async def test_refetch_book_event_toc_review_keeps_inline_validation():
    bid = uuid4()
    book = SimpleNamespace(id=bid, toc_validation="warn", toc_validation_detail="d")
    entry = MagicMock()
    entry.model_dump.return_value = {"section_title": "L1", "order_index": 0}
    inline_validation = {"verdict": "warn", "issues": ["dup pages"]}
    with patch.object(books_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(books_mod.books_repo, "get_with_toc", AsyncMock(return_value=book)), \
         patch.object(books_mod, "_enriched_toc_entries", AsyncMock(return_value=[entry])):
        data = await books_mod._refetch_book_event(
            bid, "toc_review", {"__refetch__": True, "validation": inline_validation}
        )
    # The live publisher's validation dict is small and survives inline —
    # it must win over the DB-derived replay shape.
    assert data["validation"] == inline_validation
    assert data["entries"] == [{"section_title": "L1", "order_index": 0}]


def test_job_stream_loop_refetches_marker_payloads():
    """End-to-end through the endpoint's live loop: a marker payload in the
    queue must yield the REBUILT data, not the marker."""
    jid = uuid4()
    running_job = SimpleNamespace(
        id=jid, status="running", error_message=None, phase_outputs=[]
    )
    phase = SimpleNamespace(
        phase_name="reading", phase_order=7, status="done",
        output_md="FULL", tokens_input=1, tokens_output=2,
    )
    done_job = SimpleNamespace(id=jid, phase_outputs=[phase])

    marker_payload = {
        "event": "phase_completed",
        "data": {"__refetch__": True, "phase_name": "reading", "phase_order": 7,
                 "tokens_input": 1, "tokens_output": 2},
    }
    terminal_payload = {
        "event": "job_completed",
        "data": {"job_id": str(jid), "download_url": f"/api/v1/jobs/{jid}/download"},
    }
    fake_q = SimpleNamespace(
        get=AsyncMock(side_effect=[marker_payload, terminal_payload])
    )

    async def _run():
        resp = await jobs_mod.stream_job(jid, _FakeRequest())
        return [ev async for ev in resp.body_iterator]

    with patch.object(jobs_mod, "SessionLocal", lambda: _FakeSession()), \
         patch.object(jobs_mod.jobs_repo, "get_with_phases",
                      AsyncMock(side_effect=[running_job, done_job])), \
         patch.object(jobs_mod.events_bus, "subscribe", MagicMock(return_value=fake_q)), \
         patch.object(jobs_mod.events_bus, "unsubscribe", MagicMock()):
        events = asyncio.run(_run())

    completed = [e for e in events if e["event"] == "phase_completed"]
    assert completed, events
    body = json.loads(completed[0]["data"])
    assert body["output_md"] == "FULL"
    assert "__refetch__" not in body
