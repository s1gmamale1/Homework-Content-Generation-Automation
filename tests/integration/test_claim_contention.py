"""Real-DB proof: FOR UPDATE SKIP LOCKED prevents two workers claiming one job.

Skipped unless RUN_DB_INTEGRATION=1 AND a real DATABASE_URL points at a
throwaway Postgres (the default unit suite is DB-free — tests/conftest.py).

Run:
  docker run -d --name fleet-pg -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
    -e POSTGRES_DB=edu_homework -p 5433:5432 postgres:16-alpine
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    alembic upgrade head
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_claim_contention.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_two_concurrent_claims_never_collide():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    # seed: one book, one section, two pending jobs (committed)
    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="contention-test.pdf",
            content_sha256="0" * 64,
            file_size_bytes=1,
            status="toc_ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        # Both jobs keep their server-default scheduled_at = NOW(). No past-pinning
        # crutch is needed: claim_next_job now filters `scheduled_at <= func.now()`
        # (Phase 0.5), so claimability is wholly on the DB clock and host-vs-DB skew
        # can't flake this test. This un-pinned form IS the skew regression guard.
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        book_id = book.id

    try:
        # two sessions hold their claims open simultaneously
        async with SessionLocal() as sa, SessionLocal() as sb:
            job_a = await jobs_repo.claim_next_job(sa, worker_id="A", max_attempts=3)
            # sa's row is locked-but-uncommitted; sb must SKIP it and take the other
            job_b = await jobs_repo.claim_next_job(sb, worker_id="B", max_attempts=3)
            assert job_a is not None, "worker A claimed nothing"
            assert job_b is not None, "worker B claimed nothing"
            assert job_a.id != job_b.id, "two workers claimed the SAME job"
            await sa.commit()
            await sb.commit()

        # no pending jobs left -> a third claim returns None
        async with SessionLocal() as sc:
            assert await jobs_repo.claim_next_job(sc, worker_id="C", max_attempts=3) is None
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


# ─── claim gate v2 helpers ───────────────────────────────────────────────────

def _caps_for(env: dict) -> dict:
    """Credential-only capability set a worker with this env would compute at startup."""
    from app.services import worker

    return worker._compute_capabilities(env)


_ANTHROPIC_ONLY = {"ANTHROPIC_API_KEY": "a"}
_GEMINI_ONLY = {"GEMINI_API_KEY": "g"}
_BOTH = {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"}
_NONE: dict = {}

# Attempts fence: the integration DB may have LIVE workers polling it (they
# claim with max_attempts=settings.queue_max_attempts, default 3, and filter
# `attempts < max_attempts`). Seeding test jobs with attempts=7 makes them
# invisible to real workers; the tests claim with max_attempts=8 so the gate
# under test is exercised in isolation. Seeded jobs also get a high priority
# so OUR claims always look at our rows first, and `_claim_with` rolls back
# any accidental claim of a foreign (real) pending row.
_FENCE_ATTEMPTS = 7
_FENCE_MAX = 8
_FENCE_PRIORITY = 1000


async def _seed_book(s, name: str):
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry

    book = Book(
        subject="math-algebra",
        original_filename=name,
        content_sha256="2" * 64,
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
    """Create a pending job behind the attempts fence (see _FENCE_ATTEMPTS)."""
    from app.repositories import jobs as jobs_repo

    job = await jobs_repo.create(
        s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra", **kwargs
    )
    job.attempts = _FENCE_ATTEMPTS
    job.priority = priority
    return job


async def _claim_with(caps: dict, own_ids: set, worker_id: str = "W"):
    """Claim under the gate; COMMIT only when we claimed one of our own seeded
    rows, ROLLBACK a foreign (real) row so live data is never mutated."""
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    async with SessionLocal() as s:
        job = await jobs_repo.claim_next_job(
            s, worker_id=worker_id, max_attempts=_FENCE_MAX, capabilities=caps
        )
        if job is not None and job.id not in own_ids:
            job_id = job.id
            await s.rollback()
            return job_id  # foreign row: report the id, leave it untouched
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


@pytest.mark.asyncio
async def test_api_jobs_gated_on_capabilities():
    """Fail-fast at claim time (gate v2 form of the Phase-4 test, intent
    identical): a worker with NO api capabilities must never claim a
    `transport='api'` job, even if that api job sorts first (higher priority).
    Only `transport='cli'` jobs are claimable. A worker with full caps claims
    both transports. Gating at claim covers the extract-failover path too."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "api-gate-test.pdf")
        # The api job is given higher priority so a transport-blind claim would
        # return it FIRST — proving the gate (not just luck of ordering).
        api_job = await _seed_job(
            s, book, toc, priority=_FENCE_PRIORITY + 10,
            provider="gemini", model="gemini-2.5-flash", transport="api",
            # Stamp roles so the job-column gate can route (Task 2 always stamps).
            judge_provider="claude", judge_model="claude-opus-4-7",
            extract_provider="gemini", extract_model="gemini-2.5-flash",
        )
        cli_job = await _seed_job(s, book, toc, transport="cli")
        await s.commit()
        book_id = book.id
        api_job_id = api_job.id
        cli_job_id = cli_job.id

    try:
        own = {api_job_id, cli_job_id}
        # No caps: only the cli job is ever claimable; the api job is invisible.
        first = await _claim_with(_caps_for(_NONE), own, "nokeys")
        assert first == cli_job_id, "must claim the cli job, never the api job"
        # Draining: the api job must STILL not be claimable.
        second = await _claim_with(_caps_for(_NONE), own, "nokeys")
        assert second != api_job_id, "api job must never be claimed without caps"
        assert await _status_of(api_job_id) == "pending"

        # Reset the cli job to pending so the full-caps path sees both again.
        async with SessionLocal() as s:
            cli = await s.get(HomeworkJob, cli_job_id)
            cli.status = "pending"
            cli.claimed_by = None
            cli.attempts = _FENCE_ATTEMPTS
            await s.commit()

        # With full caps: BOTH transports are claimable. Drain both.
        claimed_ids = set()
        for _ in range(2):
            claimed_ids.add(await _claim_with(_caps_for(_BOTH), own, "keys"))
        assert claimed_ids == {api_job_id, cli_job_id}, "both jobs claimable with caps"
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_api_content_with_cli_roles_claimable_gemini_only():
    """Matrix 1 (the user's exact case): api gemini content with cli judge and
    cli extract needs NO anthropic key — a gemini-only worker must claim it."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "gate2-m1.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini", model="gemini-2.5-flash",
            transport="api", extract_transport="cli", judge_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        claimed = await _claim_with(_caps_for(_GEMINI_ONLY), {job_id})
        assert claimed == job_id, "gemini-only worker must claim api+cli-roles gemini job"
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_cli_content_with_api_judge_needs_judge_capability():
    """Matrix 2: a cli-content job with judge_transport=api (judge=claude) is
    NOT claimable by a gemini-only worker but IS by an anthropic-only one."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "gate2-m2.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini", model="gemini-2.5-flash",
            transport="cli", judge_transport="api",
            # Stamp claude judge (non-self-grade for gemini content).
            judge_provider="claude", judge_model="claude-opus-4-7",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        assert await _claim_with(_caps_for(_GEMINI_ONLY), {job_id}) != job_id, (
            "gemini-only worker must NOT claim a job whose api judge is claude"
        )
        assert await _status_of(job_id) == "pending"
        claimed = await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id})
        assert claimed == job_id, "anthropic-only worker must claim it"
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_judge_pair_job_gates_on_fallback_capability():
    """Matrix 3 (§4a): a job generating ON the judge pair (claude/claude-opus-4-7)
    is stamped with judge=(claude,claude-opus-4-7) — self-grade, so the claim gate
    routes to the SELF-FALLBACK (gemini peer). An anthropic-ONLY worker must NOT
    claim it; a worker with gemini capability must.
    extract_transport=cli isolates the judge gate."""
    from app.db import SessionLocal

    _JUDGE_PROVIDER = "claude"
    _JUDGE_MODEL = "claude-opus-4-7"

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "gate2-m3.pdf")
        job = await _seed_job(
            s, book, toc,
            provider=_JUDGE_PROVIDER, model=_JUDGE_MODEL,
            transport="api", extract_transport="cli", judge_transport="api",
            # Explicitly stamp the judge as the same pair → self-grade detection fires.
            judge_provider=_JUDGE_PROVIDER, judge_model=_JUDGE_MODEL,
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        assert await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id}) != job_id, (
            "self-grade job falls back to gemini peer — anthropic-only must skip"
        )
        assert await _status_of(job_id) == "pending"
        claimed = await _claim_with(_caps_for(_BOTH), {job_id})
        assert claimed == job_id, "worker with gemini fallback capability must claim it"
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_plain_cli_job_claimable_with_no_caps():
    """Matrix 4: a plain transport=cli job (roles inherit) needs NO api
    capability at all."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "gate2-m4.pdf")
        job = await _seed_job(s, book, toc, transport="cli")
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        claimed = await _claim_with(_caps_for(_NONE), {job_id})
        assert claimed == job_id, "plain cli job must be claimable with zero caps"
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_null_model_job_with_non_self_grade_judge_claims():
    """Matrix 5 (NULL-model probe): a cli-content claude job with model=NULL and a
    stamped non-self-grade judge (gemini) must be claimable by a gemini-capable
    worker. content_model_resolved resolves NULL→claude-sonnet-4-6 (default_model),
    judge=gemini != claude generator → NOT self-grade → route on judge's cap."""
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "gate2-m5.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="claude",  # model stays NULL → resolves to claude-sonnet-4-6
            transport="cli", judge_transport="api",
            # Stamped judge: gemini (non-self) → gate needs can_gemini_api.
            judge_provider="gemini", judge_model="gemini-2.5-flash",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # anthropic-only: judge is gemini, can_gemini_api=False → skip.
        assert await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id}) != job_id, (
            "anthropic-only worker must NOT claim a job with stamped gemini judge"
        )
        assert await _status_of(job_id) == "pending"
        # gemini-only: judge is gemini, can_gemini_api=True → claim.
        claimed = await _claim_with(_caps_for(_GEMINI_ONLY), {job_id})
        assert claimed == job_id, (
            "NULL-model claude job with gemini judge must be claimable by gemini-only worker"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_api_gemini_judge_job_claimable_by_vertex_only_worker():
    """THE live bug (C1 / judge-claimgate-1): a Vertex-only worker has gemini api
    but NO anthropic key. settings.judge_provider='claude'. A job with
    transport='api', judge_provider='gemini', judge_transport='inherit' was
    STRANDED — the old gate used caps['judge_api_ok'] = cap['claude'] = False.

    After the fix: claim_next_job resolves the judge provider per-job via
    COALESCE(job.judge_provider, settings_judge_provider), then gates on the
    matching per-provider cap flag — so this job IS claimable.
    """
    from app.db import SessionLocal

    # Vertex-only: gemini api yes (SA pair), claude NO.
    vertex_env = {
        "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/sa.json",
        "GOOGLE_CLOUD_PROJECT": "my-project",
    }
    caps = _caps_for(vertex_env)
    # Sanity: credential-only caps shape — no per-role keys.
    assert caps["can_claude_api"] is False
    assert caps["can_gemini_api"] is True

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "c1-strand-test.pdf")
        # The job explicitly picks gemini as its judge provider.
        job = await _seed_job(
            s, book, toc,
            provider="gemini", model="gemini-2.5-flash",
            transport="api",
            judge_provider="gemini",  # per-job override — NOT the settings default
            judge_transport="inherit",  # resolves to 'api' since transport='api'
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        claimed = await _claim_with(caps, {job_id})
        assert claimed == job_id, (
            "Vertex-only worker must claim an api job whose judge_provider='gemini' "
            "(job-column-based gate: stamped judge_provider='gemini' → can_gemini_api required)"
        )
    finally:
        await _cleanup_book(book_id)


@pytest.mark.asyncio
async def test_stamped_judge_provider_gates_correctly():
    """Job-column-based gate: when judge_provider is explicitly stamped (via Task 2
    launch resolution or the migration backfill), the gate routes on the stamped
    value — no settings fallback needed.

    Case: job with stamped judge_provider='claude' + judge_transport='api'.
    A claude-capable worker must claim it; a gemini-only one must not.
    (Replaces the old test_null_judge_provider_falls_back_to_settings — NULL
    judge_provider no longer happens after the migration backfill + Task 2 stamping.)
    """
    from app.db import SessionLocal

    async with SessionLocal() as s:
        book, toc = await _seed_book(s, "c1-stamped-judge.pdf")
        job = await _seed_job(
            s, book, toc,
            provider="gemini", model="gemini-2.5-flash",
            transport="cli",
            judge_transport="api",
            judge_provider="claude",   # stamped explicitly
            judge_model="claude-opus-4-7",
            extract_transport="cli",
        )
        await s.commit()
        book_id, job_id = book.id, job.id

    try:
        # gemini-only worker: stamped judge_provider='claude'; can_claude_api=False → skip.
        assert await _claim_with(_caps_for(_GEMINI_ONLY), {job_id}) != job_id, (
            "gemini-only worker must NOT claim a job whose stamped judge is claude"
        )
        assert await _status_of(job_id) == "pending"

        # anthropic-only worker: stamped judge_provider='claude'; can_claude_api=True → claim.
        claimed = await _claim_with(_caps_for(_ANTHROPIC_ONLY), {job_id})
        assert claimed == job_id, (
            "anthropic-only worker must claim a job whose stamped judge_provider='claude'"
        )
    finally:
        await _cleanup_book(book_id)
