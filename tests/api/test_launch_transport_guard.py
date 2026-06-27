"""Real-DB: loud-fail guard on non-api-capable global-default role + api transport.

Guard (gate-hardening #3): after resolving judge/extract roles against the
global defaults (launch_defaults singleton), both launch handlers now run
validate_transport on the resolved (provider, model, effective_transport) pair.
A non-api provider (codex/kimi/opencode) resolving to an api effective transport
→ 400 at launch (not a silent strand). cli-resolving transports → None → no error.

Covers:
  (a) /generate: codex judge global default + judge_transport=api → 400.
  (b) /generate: default gemini judge + api job → 201 (happy path unchanged).
  (c) /jobs/batch: codex judge global default + judge_transport=api → 400.
  (d) /jobs/batch: default gemini judge + api job preview → 200 (happy path).

RED-proof: removing the guard makes (a)/(c) return 201/201 (job/batch created,
no 400). Report confirms both outcomes.

RUN_DB_INTEGRATION=1 required (real Postgres via scratch DB recipe).
"""
from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

_HDR = {"Authorization": "Bearer 123"}

# Non-api provider that IS in the manifest (so the is_valid guard passes),
# but is NOT in API_PROVIDERS → validate_transport will error on api transport.
_BAD_JUDGE_PROVIDER = "codex"
_BAD_JUDGE_MODEL = "gpt-5.5"


def _client():
    from main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _seed_book(sha_char: str):
    """Seed a toc_ready book with one lesson; return (book_id, toc_entry_id)."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha_char * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        t = TOCEntry(book_id=book.id, section_title="L0", order_index=0)
        s.add(t)
        await s.flush()
        await s.commit()
        return book.id, t.id


async def _seed_book_lessons(sha_char: str, *, n: int = 2):
    """Seed a toc_ready book with n lessons; return (book_id, [toc_entry_ids])."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="x.pdf",
                    content_sha256=sha_char * 64, file_size_bytes=1, status="toc_ready")
        s.add(book)
        await s.flush()
        toc_ids = []
        for i in range(n):
            t = TOCEntry(book_id=book.id, section_title=f"L{i}", order_index=i)
            s.add(t)
            await s.flush()
            toc_ids.append(t.id)
        await s.commit()
        return book.id, toc_ids


async def _cleanup(book_id):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(Batch).where(Batch.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _set_bad_judge_default():
    """Set launch_defaults singleton to a non-api-capable judge with api transport."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with SessionLocal() as s:
        await launch_defaults_repo.update(s, {
            "judge_provider": _BAD_JUDGE_PROVIDER,
            "judge_model": _BAD_JUDGE_MODEL,
            "judge_transport": "api",
        })
        await s.commit()


async def _restore_defaults():
    """Restore launch_defaults to the canonical seed values."""
    from app.db import SessionLocal
    from app.repositories import launch_defaults as launch_defaults_repo

    async with SessionLocal() as s:
        await launch_defaults_repo.update(s, {
            "judge_provider": "gemini",
            "judge_model": "gemini-2.5-flash",
            "judge_transport": "inherit",
        })
        await s.commit()


# ── (a) /generate: non-api judge default → 400 ───────────────────────────────

@pytest.mark.asyncio
async def test_generate_non_api_judge_default_raises_400():
    """codex judge global default + judge_transport=api → 400 before job creation.

    The job body leaves judge_provider=Auto (omitted). The handler resolves
    codex/gpt-5.5 from the singleton, computes effective transport=api (singleton
    judge_transport="api"), and validate_transport("codex", "gpt-5.5", "api")
    returns an error string → HTTPException(400). No HomeworkJob row is created.
    """
    book_id, sid = await _seed_book("Z")
    try:
        await _set_bad_judge_default()
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={"provider": "claude"},  # judge omitted → Auto → codex/api default
            )
        assert r.status_code == 400, (
            f"expected 400 from guard, got {r.status_code}: {r.text}"
        )
        detail = r.json()["detail"]
        assert "judge" in detail.lower(), f"detail should mention 'judge': {detail!r}"
        # validate_transport returns "transport=api unsupported for provider 'codex'"
        assert "api" in detail.lower() or "transport" in detail.lower(), (
            f"detail should mention api/transport mismatch: {detail!r}"
        )
    finally:
        await _restore_defaults()
        await _cleanup(book_id)


# ── (b) /generate: happy path — gemini judge + api job → 201 ─────────────────

@pytest.mark.asyncio
async def test_generate_gemini_judge_api_job_succeeds():
    """Default gemini judge (gemini-2.5-flash) + api main job must NOT trigger the guard.

    validate_transport("gemini", "gemini-2.5-flash", "api") returns None (gemini
    IS in API_PROVIDERS and model is explicit) → no error → job created normally.
    Proves the guard is zero-change on the canonical configuration.
    """
    book_id, sid = await _seed_book("9")
    try:
        # Singleton is at the default (gemini/gemini-2.5-flash/inherit).
        async with _client() as c:
            r = await c.post(
                f"/api/v1/books/{book_id}/sections/{sid}/generate",
                headers=_HDR,
                json={
                    "provider": "claude",
                    "model": "claude-opus-4-8",
                    "transport": "api",
                    # judge left Auto → resolves to gemini/gemini-2.5-flash; eff_tx=api
                    # validate_transport("gemini", "gemini-2.5-flash", "api") → None → ok
                },
            )
        assert r.status_code in (200, 201), (
            f"guard must NOT fire on gemini judge + api job; got {r.status_code}: {r.text}"
        )
    finally:
        await _cleanup(book_id)


# ── (c) /jobs/batch: non-api judge default → 400 ─────────────────────────────

@pytest.mark.asyncio
async def test_batch_non_api_judge_default_raises_400():
    """codex judge global default + judge_transport=api → 400 on batch launch.

    Same logic as (a) but exercised through the /jobs/batch endpoint.
    preview=True is used so no batch row is written even on a hypothetical pass.
    """
    book_id, _ = await _seed_book_lessons("8", n=2)
    try:
        await _set_bad_judge_default()
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "model": "claude-opus-4-8",
                    "transport": "api",
                    # judge omitted → Auto → codex/api default → guard fires
                    "preview": True,
                },
            )
        assert r.status_code == 400, (
            f"expected 400 from guard on batch, got {r.status_code}: {r.text}"
        )
        detail = r.json()["detail"]
        assert "judge" in detail.lower(), f"detail should mention 'judge': {detail!r}"
        assert "api" in detail.lower() or "transport" in detail.lower(), (
            f"detail should mention api/transport mismatch: {detail!r}"
        )
    finally:
        await _restore_defaults()
        await _cleanup(book_id)


# ── (d) /jobs/batch: happy path — gemini judge + api job → 200 ───────────────

@pytest.mark.asyncio
async def test_batch_gemini_judge_api_job_succeeds():
    """Default gemini judge + api batch must NOT trigger the guard (preview=True
    so no real batch row is written, but the guard runs before the preview check).
    """
    book_id, _ = await _seed_book_lessons("7", n=2)
    try:
        async with _client() as c:
            r = await c.post(
                "/api/v1/jobs/batch",
                headers=_HDR,
                json={
                    "book_id": str(book_id),
                    "provider": "claude",
                    "model": "claude-opus-4-8",
                    "transport": "api",
                    "preview": True,
                },
            )
        assert r.status_code == 200, (
            f"guard must NOT fire on gemini judge + api batch; got {r.status_code}: {r.text}"
        )
    finally:
        await _cleanup(book_id)
