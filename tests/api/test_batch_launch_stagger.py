"""Batch-launch wave stagger — endpoint wiring, both directions."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

BOOK_ID = uuid.uuid4()


@pytest.fixture
def fake_session():
    """`launch_batch` really calls `session.flush()` (batch.py:423) and
    `session.commit()` (:427). Without this override those run on the REAL
    session built from the sentinel `DATABASE_URL`, which points at a database
    that does not exist — see the warning in `tests/api/conftest.py`'s
    docstring. Every other mocked 201-path launch test overrides it the same
    way (`test_batch_output_language.py:130-143`); do not skip it."""
    from app.db import get_session
    from main import app

    def _mk():
        s = MagicMock()
        s.commit = AsyncMock()
        s.flush = AsyncMock()
        s.rollback = AsyncMock()
        s.close = AsyncMock()
        return s

    async def _override():
        yield _mk()

    app.dependency_overrides[get_session] = _override
    yield
    app.dependency_overrides.pop(get_session, None)

# `launch_batch` ends by building its response through `_rollup_payload`
# (batch.py:429 -> :102-133), which reads 21 attributes off the batch.
# A 4-attribute stub makes every test in this file 500 inside payload
# construction — i.e. fail GREEN for a reason that has nothing to do with the
# stagger. This mirrors the complete shape already proven at
# tests/api/test_never_pay_twice.py:55-77.
_FAKE_BATCH = SimpleNamespace(
    id=uuid.uuid4(),
    book_id=BOOK_ID,
    subject="geografiya",
    grade="5",
    output_language="ru",
    provider="gemini",
    model="gemini-3.6-flash",
    transport="cli",
    extract_transport="api",
    judge_transport="inherit",
    solver_transport="inherit",
    extract_provider="gemini",
    extract_model="gemini-3.5-flash-lite",
    judge_provider="claude",
    judge_model="claude-sonnet-4-6",
    solver_provider="claude",
    solver_model="claude-haiku-4-5-20251001",
    created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    paused_at=None,
    paused_reason=None,
    session_limit_strategy="inherit",
    kind="homework",
)
_ROWS = [
    SimpleNamespace(id=uuid.uuid4(), section_number=f"1.{i}",
                    section_title=f"Dars {i}", page_start=i, page_end=i + 1,
                    order_index=i)
    for i in range(1, 15)          # 14 plain lesson rows
]


def _fake_book():
    return SimpleNamespace(id=BOOK_ID, status="toc_ready", subject="geografiya",
                           grade="5", source_language="ru",
                           original_filename="g.pdf")


def _fake_launch_defaults():
    return SimpleNamespace(
        judge_provider="claude", judge_model="claude-sonnet-4-6", judge_transport="inherit",
        extract_provider="gemini", extract_model="gemini-3.5-flash-lite", extract_transport="api",
        solver_provider="claude", solver_model="claude-haiku-4-5-20251001", solver_transport="inherit",
        output_language="ru",
    )


def _wire(monkeypatch, batch_mod, *, offsets_sink, latest=None, resume_ids=None):
    """`resume_ids`: when given, only those toc ids resolve to a saved
    failed job (-> resume branch); every other target falls through to
    create. That mix is what makes the SHARED wave counter observable."""
    async def _get_book(session, book_id):
        return _fake_book()

    async def _list_for_book(session, book_id):
        return list(_ROWS)

    async def _get_ld(session):
        return _fake_launch_defaults()

    async def _find_active(session, book_id, toc_entry_id, *, transport=None,
                           output_language, kind="homework"):
        return None

    async def _latest(session, book_id, toc_entry_id, *, transport=None,
                      output_language, kind="homework"):
        if resume_ids is not None:
            return latest if toc_entry_id in resume_ids else None
        return latest

    async def _lock(session, book_id, toc_entry_id=None):
        return None

    async def _create(session, **kwargs):
        offsets_sink.append(kwargs.get("start_offset_seconds"))
        return SimpleNamespace(id=uuid.uuid4())

    async def _get_or_create_batch(session, **kwargs):
        return _FAKE_BATCH

    async def _rollup(session, batch_id):
        return {}

    async def _archive_rollup(session, batch_id):
        return {"archived": 0, "unarchived": 0, "stale": 0}

    async def _toc_total(session, batch_id):
        return len(_ROWS)

    monkeypatch.setattr(batch_mod.books_repo, "get", _get_book)
    monkeypatch.setattr(batch_mod.books_repo, "lock_book_shared",
                        lambda session, book_id: _lock(session, book_id))
    monkeypatch.setattr(batch_mod.toc_repo, "list_for_book", _list_for_book)
    monkeypatch.setattr(batch_mod.launch_defaults_repo, "get", _get_ld)
    monkeypatch.setattr(batch_mod.jobs_repo, "find_active_for_section", _find_active)
    monkeypatch.setattr(batch_mod.jobs_repo, "latest_for_section", _latest)
    monkeypatch.setattr(batch_mod.jobs_repo, "lock_section_for_generate", _lock)
    monkeypatch.setattr(batch_mod.jobs_repo, "create", _create)
    monkeypatch.setattr(batch_mod.batches_repo, "get_or_create_for_book",
                        _get_or_create_batch)
    monkeypatch.setattr(batch_mod.batches_repo, "rollup_for_batch", _rollup)
    monkeypatch.setattr(batch_mod.batches_repo, "archive_rollup_for_batch",
                        _archive_rollup)
    monkeypatch.setattr(batch_mod.batches_repo, "toc_total_for_batch", _toc_total)


async def _launch(payload):
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        return await c.post("/api/v1/jobs/batch",
                            headers={"Authorization": "Bearer 123"},
                            json=payload)


@pytest.mark.asyncio
async def test_large_launch_is_spread_across_waves(monkeypatch, fake_session):
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    # 14 lessons at wave 6 -> 6 x 0, 6 x 60, 2 x 120
    assert offsets == [0] * 6 + [60] * 6 + [120] * 2
    assert resp.json()["stagger"] == {
        "wave_size": 6, "interval_seconds": 60, "jobs_launched": 14,
        "waves": 3, "last_start_offset_seconds": 120}


@pytest.mark.asyncio
async def test_small_launch_is_not_staggered_at_all(monkeypatch, fake_session):
    """The other direction: a launch that fits in one wave is untouched."""
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID),
                          "toc_entry_ids": [str(r.id) for r in _ROWS[:5]]})

    assert resp.status_code == 201
    assert offsets == [0, 0, 0, 0, 0]
    assert resp.json()["stagger"]["waves"] == 1
    assert resp.json()["stagger"]["last_start_offset_seconds"] == 0


@pytest.mark.asyncio
async def test_kill_switch_disables_the_stagger(monkeypatch, fake_session):
    from app.api.v1 import batch as batch_mod
    offsets = []
    _wire(monkeypatch, batch_mod, offsets_sink=offsets)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 0)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    assert offsets == [0] * 14


@pytest.mark.asyncio
async def test_resumed_sections_share_the_same_wave_counter(monkeypatch, fake_session):
    """A resumed job is as claimable as a created one, so both must advance the
    counter — otherwise a resume-heavy relaunch rebuilds the herd."""
    from app.api.v1 import batch as batch_mod
    offsets = []
    resume_offsets = []
    saved = SimpleNamespace(id=uuid.uuid4(), status="failed", provider="gemini",
                            model="gemini-3.6-flash", extract_provider=None,
                            extract_model=None, judge_provider=None,
                            judge_model=None, solver_provider=None,
                            solver_model=None)
    _wire(monkeypatch, batch_mod, offsets_sink=offsets, latest=saved)

    async def _reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        resume_offsets.append(start_offset_seconds)

    monkeypatch.setattr(batch_mod.jobs_repo, "reset_for_retry", _reset)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 3)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    assert offsets == []                      # everything resumed, nothing created
    assert resume_offsets[:7] == [0, 0, 0, 60, 60, 60, 120]


@pytest.mark.asyncio
async def test_mixed_resume_and_create_share_one_wave_sequence(monkeypatch, fake_session):
    """THE test for the shared counter, and the only one that can catch the
    plausible wrong implementation (a separate counter per branch).

    The two single-disposition tests above cannot: in an all-create launch
    `created == launched` at every call site, and in an all-resume launch the
    create branch never runs. Only a MIX distinguishes them.

    First 4 targets resume, remaining 10 create. With wave_size 3 the resumed
    and created offsets must interleave into ONE monotone sequence
    [(i // 3) * 60 for i in range(14)] — not two independent ramps that both
    restart at 0.
    """
    from app.api.v1 import batch as batch_mod
    offsets = []
    resume_offsets = []
    resume_ids = {r.id for r in _ROWS[:4]}
    saved = SimpleNamespace(id=uuid.uuid4(), status="failed", provider="gemini",
                            model="gemini-3.6-flash", extract_provider=None,
                            extract_model=None, judge_provider=None,
                            judge_model=None, solver_provider=None,
                            solver_model=None)
    _wire(monkeypatch, batch_mod, offsets_sink=offsets, latest=saved,
          resume_ids=resume_ids)

    async def _reset(session, job_id, batch_id=None, *, start_offset_seconds=0):
        resume_offsets.append(start_offset_seconds)

    monkeypatch.setattr(batch_mod.jobs_repo, "reset_for_retry", _reset)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 3)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    resp = await _launch({"book_id": str(BOOK_ID)})

    assert resp.status_code == 201
    assert resume_offsets == [0, 0, 0, 60]
    assert offsets == [60, 60, 120, 120, 120, 180, 180, 180, 240, 240]
    # The whole point: ONE sequence, not two.
    assert resume_offsets + offsets == [(i // 3) * 60 for i in range(14)]
    body = resp.json()
    assert body["jobs_resumed"] == 4
    assert body["jobs_created"] == 10
    assert body["stagger"]["jobs_launched"] == 14


@pytest.mark.asyncio
async def test_resume_endpoint_passes_the_wave_settings(monkeypatch):
    from app.api.v1 import batch as batch_mod
    from main import app

    batch_id = uuid.uuid4()
    seen = {}

    async def _resume(session, bid, *, wave_size=0, interval_seconds=0):
        seen["wave_size"] = wave_size
        seen["interval_seconds"] = interval_seconds
        return {"resumed": 7, "skipped_retired": []}

    async def _lock(session, book_id):
        return None

    monkeypatch.setattr(batch_mod.jobs_repo, "resume_failed_in_batch", _resume)
    monkeypatch.setattr(batch_mod.books_repo, "lock_book_shared", _lock)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_size", 6)
    monkeypatch.setattr(batch_mod.settings, "batch_launch_wave_interval_seconds", 60)

    async def _get(model, pk):
        return SimpleNamespace(id=batch_id, book_id=BOOK_ID)

    class _FakeSession:
        async def get(self, model, pk):
            return await _get(model, pk)

        def expire(self, obj):
            return None

        async def commit(self):
            return None

    app.dependency_overrides[batch_mod.get_session] = lambda: _FakeSession()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as c:
            resp = await c.post(f"/api/v1/jobs/batch/{batch_id}/resume",
                                headers={"Authorization": "Bearer 123"})
    finally:
        app.dependency_overrides.pop(batch_mod.get_session, None)

    assert resp.status_code == 200
    assert seen == {"wave_size": 6, "interval_seconds": 60}
    body = resp.json()
    assert body["jobs_resumed"] == 7
    # 7 jobs at wave 6 -> last one is in wave 1
    assert body["stagger"]["waves"] == 2
    assert body["stagger"]["last_start_offset_seconds"] == 60
