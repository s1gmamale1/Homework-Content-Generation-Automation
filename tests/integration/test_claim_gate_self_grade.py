"""Real-DB proof: job-column-based self-grade claim gate (Task 4).

Tests that `claim_next_job` correctly identifies self-grade jobs from STAMPED
job columns and routes to the self-fallback peer's capability — rather than
relying on settings hints. Includes the NULL-content-model case (case d) which
requires `content_model_resolved` (a CASE expression over MODEL_MANIFEST) rather
than a bare COALESCE.

Run:
  createdb -U macmini5 edu_gld_test
  DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev alembic upgrade head
  DATABASE_URL=postgresql+asyncpg://macmini5@localhost:5432/edu_gld_test \\
    RUN_DB_INTEGRATION=1 uv run --extra dev python -m pytest \\
    tests/integration/test_claim_gate_self_grade.py -v
  dropdb -U macmini5 edu_gld_test
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

_FENCE_ATTEMPTS = 7
_FENCE_MAX = 8
_FENCE_PRIORITY = 1000

_ANTHROPIC_ONLY = {"ANTHROPIC_API_KEY": "a"}
_GEMINI_ONLY = {"GEMINI_API_KEY": "g"}
_BOTH = {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"}
_NONE: dict = {}


def _caps_for(env: dict) -> dict:
    """Credential-only capability dict (Task 4 new shape)."""
    from app.services.worker import _compute_capabilities

    return _compute_capabilities(env)


async def _seed_book(s, name: str):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256="3" * 64,
        file_size_bytes=1,
        status="toc_ready",
    )
    s.add(book)
    await s.flush()
    toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
    s.add(toc)
    await s.flush()
    return book, toc


async def _cleanup_book(book_id):
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as s:
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
        await s.execute(delete(Book).where(Book.id == book_id))
        await s.commit()


async def _seed_job(s, book, toc, *, priority: int = _FENCE_PRIORITY, **kwargs):
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra", **kwargs
    )
    job.attempts = _FENCE_ATTEMPTS
    job.priority = priority
    return job


async def _claim_with(caps: dict, own_ids: set, worker_id: str = "W"):
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        job = await jobs_repo.claim_next_job(
            s, worker_id=worker_id, max_attempts=_FENCE_MAX, capabilities=caps
        )
        if job is not None and job.id not in own_ids:
            await s.rollback()
            return job.id  # foreign row — leave untouched
        if job is not None:
            job_id = job.id
            await s.commit()
            return job_id
        await s.commit()
        return None


async def _status_of(job_id):
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        return await jobs_repo.get_status(s, job_id)


# ─── Test cases ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_a_non_self_grade_api_judge_gemini():
    """Case (a): non-self-grade, stamped judge_provider=gemini + judge_transport=api.
    Must be claimable by can_gemini_api=True, NOT by can_claude_api-only.

    BITE: flip can_gemini_api → job NOT claimed.
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "sg-case-a.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="claude", model="claude-sonnet-4-6",  # content = claude
            transport="cli",
            judge_provider="gemini",            # non-self judge
            judge_model="gemini-2.5-flash",
            judge_transport="api",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # BITE: claude-only worker — judge is gemini, can_gemini_api=False → skip.
        assert await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id}) != job_id, (
            "case (a) BITE: claude-only worker must NOT claim a gemini-judge job"
        )
        assert await _status_of(job_id) == "pending"

        # Correct worker: can_gemini_api=True → claim.
        claimed = await _claim_with(_caps_for(_GEMINI_ONLY), {job_id})
        assert claimed == job_id, (
            "case (a): gemini-api worker must claim a non-self-grade gemini-judge job"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_case_b_self_grade_claude_opus_needs_gemini_peer():
    """Case (b): self-grade — content=claude/claude-opus-4-7, judge=same.
    Self-fallback peer = gemini (the alternate when generator IS the primary peer).
    Gate must require can_gemini_api.

    BITE: flip can_gemini_api=False → job NOT claimed.
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "sg-case-b.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="claude", model="claude-opus-4-7",
            transport="api",
            judge_provider="claude", judge_model="claude-opus-4-7",  # self-grade
            judge_transport="api",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # BITE: claude-only → gemini peer unavailable → skip.
        assert await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id}) != job_id, (
            "case (b) BITE: claude-only worker must NOT claim claude/opus self-grade "
            "(judge falls back to gemini peer)"
        )
        assert await _status_of(job_id) == "pending"

        # gemini-only: needs can_gemini_api for the gemini peer fallback.
        # BUT also needs can_claude_api for content (transport=api, provider=claude).
        # So only _BOTH works here.
        claimed = await _claim_with(_caps_for(_BOTH), {job_id})
        assert claimed == job_id, (
            "case (b): worker with both caps must claim claude/opus self-grade job"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_case_c_self_grade_gemini_flash_needs_claude_peer():
    """Case (c): self-grade — content=gemini/gemini-2.5-flash, judge=same.
    Self-fallback peer = claude/claude-opus-4-7 (the primary peer, since generator
    != the primary peer). Gate must require can_claude_api.

    BITE: flip can_claude_api=False → job NOT claimed.
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "sg-case-c.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini", model="gemini-2.5-flash",
            transport="cli",
            judge_provider="gemini", judge_model="gemini-2.5-flash",  # self-grade
            judge_transport="api",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # BITE: gemini-only → claude peer unavailable → skip.
        assert await _claim_with(_caps_for(_GEMINI_ONLY), {job_id}) != job_id, (
            "case (c) BITE: gemini-only worker must NOT claim gemini/flash self-grade "
            "(judge falls back to claude/opus peer)"
        )
        assert await _status_of(job_id) == "pending"

        # anthropic-only: needs can_claude_api for the claude peer fallback.
        claimed = await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id})
        assert claimed == job_id, (
            "case (c): anthropic-only worker must claim gemini/flash self-grade job"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_case_d_auto_content_model_null_self_grade_needs_claude_peer():
    """Case (d) — THE KEY CASE: provider='gemini', model=NULL (Auto), judge stamped
    as gemini/gemini-3.1-pro-preview (= default_model('gemini')).

    `content_model_resolved` must make the SQL see this as self-grade:
      model IS NULL AND provider='gemini' → 'gemini-3.1-pro-preview'
      judge_model = 'gemini-3.1-pro-preview' → MATCH → self-grade.
    Self-fallback peer = claude → gate requires can_claude_api=True.

    BITE-PROOF: reverting to coalesce(model,'') would produce '' != 'gemini-3.1-...'
    → NOT self-grade → gate routes on judge_provider='gemini' → needs can_gemini_api
    → anthropic-only worker claims it (WRONG). Test must FAIL with coalesce.
    """
    from app.db import SessionLocal
    from app.services.agent_models import default_model

    gemini_default = default_model("gemini")
    assert gemini_default == "gemini-3.1-pro-preview", (
        f"Precondition: default_model('gemini') must be 'gemini-3.1-pro-preview'; got {gemini_default!r}"
    )

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "sg-case-d.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini",       # model stays NULL (Auto)
            transport="cli",
            judge_provider="gemini",
            judge_model=gemini_default,  # 'gemini-3.1-pro-preview'
            judge_transport="api",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # BITE: gemini-only → self-grade detected → needs claude peer → skip.
        assert await _claim_with(_caps_for(_GEMINI_ONLY), {job_id}) != job_id, (
            "case (d) BITE: gemini-only worker must NOT claim auto-model gemini job "
            "whose judge equals default_model('gemini') "
            "(content_model_resolved sees this as self-grade → needs claude peer)"
        )
        assert await _status_of(job_id) == "pending"

        # anthropic-only: can_claude_api=True → can serve the claude peer fallback.
        claimed = await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id})
        assert claimed == job_id, (
            "case (d): anthropic-only worker must claim auto-model gemini self-grade job "
            "(content_model_resolved correctly identifies self-grade)"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_case_d_coalesce_fallback_misclassifies():
    """Bite-proof for case (d): shows that using coalesce(model,'') instead of
    content_model_resolved causes misclassification.

    With coalesce: content_model = '' != 'gemini-3.1-pro-preview' → NOT self-grade
    → gate routes on judge_provider='gemini' → needs can_gemini_api only
    → gemini-only worker CAN claim (WRONG behavior).

    This test patches claim_next_job's SQL to use coalesce and verifies that the
    misclassification occurs (a gemini-only worker claims the job that should be
    blocked). The patched version MUST produce a different result than the correct
    implementation.

    IMPORTANT: this test asserts the WRONG behavior (claim succeeds with wrong caps)
    to prove the coalesce approach is broken. The real test is test_case_d_auto_content_model_null_self_grade_needs_claude_peer.
    """
    from app.db import SessionLocal
    from app.services.agent_models import default_model
    from sqlalchemy import and_, case, func, literal, not_, or_, select, update
    from sqlalchemy import case as sa_case

    gemini_default = default_model("gemini")

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "sg-case-d-coalesce.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini",   # model stays NULL
            transport="cli",
            judge_provider="gemini",
            judge_model=gemini_default,  # 'gemini-3.1-pro-preview'
            judge_transport="api",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    # Verify: with the broken coalesce approach, a gemini-only worker CAN claim it
    # (misclassification — it should need claude but coalesce doesn't detect self-grade).
    try:
        from app.db import SessionLocal as SL
        from app.models.homework_job import HomeworkJob
        from app.services.model_tiers import _PRIMARY_SELF_FALLBACK

        caps = _caps_for(_GEMINI_ONLY)  # can_claude_api=False, can_gemini_api=True

        async with SL() as s:
            # Reproduce the broken coalesce gate inline.
            judge_needs_api = or_(
                HomeworkJob.judge_transport == "api",
                and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"),
            )

            def _provider_api_ok_coalesce(resolved):
                return or_(
                    and_(resolved == "claude", literal(bool(caps.get("can_claude_api")))),
                    and_(resolved == "gemini", literal(bool(caps.get("can_gemini_api")))),
                )

            # BROKEN: use coalesce(model,'') instead of content_model_resolved CASE.
            broken_content_model = func.coalesce(HomeworkJob.model, "")
            job_is_self_grade_broken = and_(
                HomeworkJob.provider == HomeworkJob.judge_provider,
                broken_content_model == func.coalesce(HomeworkJob.judge_model, ""),
            )
            self_grade_judge_provider_broken = sa_case(
                (and_(HomeworkJob.provider == _PRIMARY_SELF_FALLBACK[0],
                      broken_content_model == _PRIMARY_SELF_FALLBACK[1]), "gemini"),
                else_="claude",
            )
            judge_ok_broken = or_(
                not_(judge_needs_api),
                and_(job_is_self_grade_broken, _provider_api_ok_coalesce(self_grade_judge_provider_broken)),
                and_(not_(job_is_self_grade_broken), _provider_api_ok_coalesce(HomeworkJob.judge_provider)),
            )
            extract_needs_api = or_(
                HomeworkJob.extract_transport == "api",
                and_(HomeworkJob.extract_transport == "inherit", HomeworkJob.transport == "api"),
            )
            content_ok = or_(
                HomeworkJob.transport == "cli",
                and_(HomeworkJob.provider == "claude", literal(bool(caps.get("can_claude_api")))),
                and_(HomeworkJob.provider == "gemini", literal(bool(caps.get("can_gemini_api")))),
            )
            extract_ok = or_(
                not_(extract_needs_api),
                _provider_api_ok_coalesce(HomeworkJob.extract_provider),
            )

            # Build the broken claim query.
            pick_stmt = (
                select(HomeworkJob.id)
                .where(HomeworkJob.id == job_id)  # target our job only
                .where(HomeworkJob.status == "pending")
                .where(HomeworkJob.scheduled_at <= func.now())
                .where(HomeworkJob.attempts < _FENCE_MAX)
                .where(content_ok)
                .where(judge_ok_broken)
                .where(extract_ok)
                .with_for_update(skip_locked=True)
            )
            found_id = (await s.execute(pick_stmt)).scalar_one_or_none()
            await s.rollback()

        # The broken gate SHOULD find the job (misclassification).
        assert found_id == job_id, (
            "Bite-proof FAILED: the coalesce gate should misclassify the auto-model job "
            "as NOT self-grade and allow a gemini-only worker to select it. "
            f"Expected job_id={job_id}, got {found_id!r}. "
            "This indicates content_model_resolved and coalesce produce the SAME result "
            "for this case — the test setup or assertion is wrong."
        )

    finally:
        await _cleanup_book(book_id)
