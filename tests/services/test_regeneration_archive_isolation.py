"""A revision must never reach the LEGACY Notion archive.

`notion_archive.archive_job` writes the lesson's existing `Homework` page. That
page IS version 1 — the thing versioned regeneration exists to preserve — so
pushing a revision through it would destroy V1 and leave no version at all.
The pipeline already avoids calling it for a revision, but that is only the
AUTOMATIC caller: the operator retry route, the force route and the batch sweep
all call `archive_job` directly, and `force=True` deliberately bypasses every
idempotency guard in the function. The guard therefore has to be INTRINSIC —
inside `archive_job`, before lesson/page identity is resolved and before a
Notion client exists.

$0: no Notion client is ever constructed here, which is exactly what the
assertions check.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import notion_archive


@pytest.fixture()
def env(monkeypatch):
    ns = SimpleNamespace()
    ns.skip_reasons: list[tuple] = []
    ns.job = SimpleNamespace(
        id=uuid.uuid4(), book_id=uuid.uuid4(), toc_entry_id=uuid.uuid4(),
        subject="math-algebra", status="done", output_language="uz",
        kind="homework", notion_archived_at=None, claim_token=None,
        revision_of_job_id=uuid.uuid4(), regeneration_target_id=uuid.uuid4(),
        created_at=None,
    )
    from app.config import settings

    monkeypatch.setattr(settings, "notion_enabled", True)
    monkeypatch.setattr(settings, "notion_api_key", "secret-not-used")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    ns.session = session
    monkeypatch.setattr(
        notion_archive, "SessionLocal", MagicMock(return_value=session))
    monkeypatch.setattr(
        notion_archive.jobs_repo, "get", AsyncMock(return_value=ns.job))

    async def _skip(session, job_id, reason):
        ns.skip_reasons.append((job_id, reason))

    monkeypatch.setattr(notion_archive.jobs_repo, "set_notion_skip_reason", _skip)

    # Anything past the guard would need these. They must never be reached.
    def _boom(*a, **k):
        raise AssertionError(
            "a revision reached lesson/page identity resolution or a Notion "
            "client — the legacy archive would overwrite the immutable V1 page")

    monkeypatch.setattr(notion_archive.books_repo, "get", _boom)
    monkeypatch.setattr(notion_archive.toc_repo, "get", _boom)
    monkeypatch.setattr(notion_archive, "_resolve_subject_page_id", _boom)
    monkeypatch.setattr(notion_archive, "NotionClientWrapper", _boom)
    monkeypatch.setattr(notion_archive, "find_or_create", _boom)
    return ns


async def test_a_revision_is_refused_with_a_deterministic_skip_reason(env):
    await notion_archive.archive_job(env.job.id)
    assert env.skip_reasons == [
        (env.job.id, notion_archive.REVISION_SKIP_REASON)]
    assert notion_archive.REVISION_SKIP_REASON == (
        "regeneration revision: use versioned publisher")


async def test_force_does_not_bypass_the_revision_guard(env):
    """`force=True` is the direction-blind operator override — it skips the
    already-archived early return and CLEARS the leaf page before rewriting it.
    That is precisely the path that must not reach a revision."""
    env.job.notion_archived_at = "2026-08-01T00:00:00Z"
    await notion_archive.archive_job(env.job.id, force=True)
    assert env.skip_reasons == [
        (env.job.id, notion_archive.REVISION_SKIP_REASON)]


async def test_a_valid_claim_token_does_not_bypass_the_revision_guard(env):
    """The fenced automatic path: a token that WOULD pass `_claim_token_ok`."""
    token = uuid.uuid4()
    env.job.claim_token = token
    await notion_archive.archive_job(env.job.id, claim_token=token)
    assert env.skip_reasons == [
        (env.job.id, notion_archive.REVISION_SKIP_REASON)]


async def test_the_guard_runs_before_the_claim_token_check(env):
    """Even a STALE token gets the deterministic skip reason, so a revision is
    never silently dropped without a recorded explanation."""
    env.job.claim_token = uuid.uuid4()
    await notion_archive.archive_job(env.job.id, claim_token=uuid.uuid4())
    assert env.skip_reasons == [
        (env.job.id, notion_archive.REVISION_SKIP_REASON)]


async def test_notion_globally_disabled_still_does_NO_db_work(env, monkeypatch):
    """The existing zero-DB early return is preserved: with Notion off, the
    guard must not start opening sessions to write skip reasons."""
    from app.config import settings

    monkeypatch.setattr(settings, "notion_enabled", False)
    monkeypatch.setattr(
        notion_archive.jobs_repo, "get",
        AsyncMock(side_effect=AssertionError("no DB work when Notion is off")))
    await notion_archive.archive_job(env.job.id, force=True)
    assert env.skip_reasons == []


async def test_an_ordinary_job_passes_the_guard(env):
    """The guard is keyed on `revision_of_job_id` alone — an ordinary job must
    proceed exactly as before.

    `archive_job` swallows everything into its own "archive error" skip, so
    tripping the sentinel and landing on THAT reason (rather than the revision
    one) is the proof it got past the guard instead of being turned away by it.
    """
    env.job.revision_of_job_id = None
    env.job.regeneration_target_id = None
    await notion_archive.archive_job(env.job.id)
    assert env.skip_reasons == [(env.job.id, "archive error")]
