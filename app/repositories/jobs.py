from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, case, func, literal, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Batch, HomeworkJob, PhaseOutput, TOCEntry
from app.repositories import phase_outputs as phase_repo
from app.repositories import workers as workers_repo

_TERMINAL_STATUSES = ("done", "failed", "cancelled")


def status_write_allowed(current: str, target: str) -> bool:
    """Guard for job status writes (cancel-race-1). A terminal status is frozen;
    a `cancelling` job may only advance to `cancelled` (never resurrected to
    running/pending, nor flipped to done/failed). Every other transition is
    allowed. Mirror this rule in the set_status guarded UPDATE WHERE clause."""
    if current in _TERMINAL_STATUSES:
        return False
    if current == "cancelling" and target != "cancelled":
        return False
    return True


async def create(
    session: AsyncSession,
    *,
    book_id: UUID,
    toc_entry_id: UUID,
    subject: str,
    output_language: str,
    status: str = "pending",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    batch_id: Optional[UUID] = None,
    transport: str = "cli",
    extract_transport: str = "inherit",
    judge_transport: str = "inherit",
    solver_transport: str = "inherit",
    custom_prompts: Optional[dict] = None,
    selected_phases: Optional[list] = None,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    solver_provider: Optional[str] = None,
    solver_model: Optional[str] = None,
) -> HomeworkJob:
    kwargs: dict[str, Any] = dict(
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=subject,
        output_language=output_language,
        status=status,
        transport=transport,
        extract_transport=extract_transport,
        judge_transport=judge_transport,
        solver_transport=solver_transport,
    )
    if provider is not None:
        kwargs["provider"] = provider
    if model is not None:
        kwargs["model"] = model
    if batch_id is not None:
        kwargs["batch_id"] = batch_id
    if custom_prompts is not None:
        kwargs["custom_prompts"] = custom_prompts
    if selected_phases is not None:
        kwargs["selected_phases"] = selected_phases
    for _k, _v in (
        ("extract_provider", extract_provider),
        ("extract_model", extract_model),
        ("judge_provider", judge_provider),
        ("judge_model", judge_model),
        ("solver_provider", solver_provider),
        ("solver_model", solver_model),
    ):
        if _v is not None:
            kwargs[_k] = _v
    job = HomeworkJob(**kwargs)
    session.add(job)
    await session.flush()
    return job


async def get(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    return await session.get(HomeworkJob, job_id)


async def get_with_phases(session: AsyncSession, job_id: UUID) -> Optional[HomeworkJob]:
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .options(selectinload(HomeworkJob.phase_outputs))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_active_for_section(
    session: AsyncSession,
    book_id: UUID,
    toc_entry_id: UUID,
    *,
    transport: Optional[str] = None,
    output_language: str,
) -> Optional[HomeworkJob]:
    """Return the most recent pending/running/done job for the (book, section).

    `done` is included so idempotent regenerate returns the existing successful
    result. Callers that want to force a new run must pass `force=True` and skip
    this lookup entirely.

    When `transport` is given, the lookup is scoped to jobs of that transport —
    so an api batch over a cli-generated book doesn't find the cli jobs and skip
    every lesson (spec §9a).

    `output_language` scopes the lookup so a job in another language is NOT
    adopted — an 'en' batch must never reuse a 'uz' job (spec §language-key).
    """
    conds = [
        HomeworkJob.book_id == book_id,
        HomeworkJob.toc_entry_id == toc_entry_id,
        HomeworkJob.status.in_(["pending", "running", "done"]),
        HomeworkJob.output_language == output_language,
    ]
    if transport is not None:
        conds.append(HomeworkJob.transport == transport)
    stmt = (
        select(HomeworkJob)
        .where(*conds)
        .order_by(HomeworkJob.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_section_for_generate(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID
) -> None:
    """Serialize generate calls for the same (book, section) using a Postgres
    transaction-scoped advisory lock. The lock is auto-released on commit /
    rollback, so a fast follow-up request waits behind the in-flight one and
    then sees the just-created job via `find_active_for_section`.

    Without this lock, two concurrent POSTs (e.g., a double-click) both
    observe "no active job" and both insert — producing duplicate jobs that
    waste Gemini calls and confuse the SSE consumer.

    Uses `pg_advisory_xact_lock(bigint)` with a key derived from blake2b
    of the (book_id, toc_entry_id) pair so it's stable across requests and
    collision-resistant across other lock users in the same database.
    """
    import hashlib

    digest = hashlib.blake2b(
        f"generate:{book_id}:{toc_entry_id}".encode(),
        digest_size=8,
    ).digest()
    # Postgres bigint is signed 64-bit. blake2b digest_size=8 → 8 bytes →
    # int.from_bytes(signed=True) gives a value in [-2^63, 2^63).
    key = int.from_bytes(digest, "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


async def set_status(
    session: AsyncSession,
    job_id: UUID,
    status: str,
    *,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    error_message: Optional[str] = None,
    current_phase: Optional[str] = None,
    guard: bool = True,
) -> bool:
    """Set a job's status. With ``guard`` (default), a guarded UPDATE refuses to
    overwrite a terminal status or resurrect a `cancelling` job (cancel-race-1):
    terminal {done,failed,cancelled} is frozen; from `cancelling` only
    `cancelled` is allowed. Mirrors ``status_write_allowed``. Returns True iff a
    row was updated. claim (pending->running) and reset_for_retry
    (cancelled->pending) use their OWN updates and are unaffected."""
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if error_message is not None:
        values["error_message"] = error_message
    if current_phase is not None:
        values["current_phase"] = current_phase
    stmt = update(HomeworkJob).where(HomeworkJob.id == job_id)
    if guard:
        stmt = stmt.where(HomeworkJob.status.not_in(_TERMINAL_STATUSES))
        if status != "cancelled":
            stmt = stmt.where(HomeworkJob.status != "cancelling")
    result = await session.execute(stmt.values(**values))
    return result.rowcount > 0


async def set_notion_archived(
    session: AsyncSession, job_id: UUID, notion_archived_at: datetime
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_archived_at = notion_archived_at
    job.notion_skip_reason = None   # success clears any prior skip marker


async def set_notion_skip_reason(
    session: AsyncSession, job_id: UUID, reason: Optional[str]
) -> None:
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return
    job.notion_skip_reason = reason


async def reset_for_retry(
    session: AsyncSession, job_id: UUID, batch_id: Optional[UUID] = None
) -> Optional[HomeworkJob]:
    """Reset a failed job back to 'pending' so the worker can re-claim it.

    Clears `error_message`, `current_phase`, `started_at`, `completed_at`, and
    resets the queue retry counter (`attempts`) so the worker treats this as a
    fresh attempt rather than counting it against `queue_max_attempts`. The
    pipeline is idempotent against existing phase rows (`phase_repo.create_or_reset`
    handles the upsert), so no phase-output cleanup is needed here.

    ``batch_id``: when a batch launch RESUMES a prior failed/cancelled job into
    a batch, stamp that batch so the resumed job is counted in the batch rollup
    (Monitor) and reachable by batch-level controls. Without this, a resumed job
    keeps its old (often NULL) batch_id and runs invisibly to the batch — the
    only-stamp-if-provided guard keeps non-batch resumes (single /generate)
    untouched.

    Returns the updated row, or None if the job no longer exists.
    """
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return None
    job.status = "pending"
    job.error_message = None
    job.current_phase = None
    job.started_at = None
    job.completed_at = None
    job.attempts = 0
    if batch_id is not None:
        job.batch_id = batch_id
    return job


async def list_running_for_sweep(session: AsyncSession) -> list[HomeworkJob]:
    stmt = select(HomeworkJob).where(HomeworkJob.status.in_(["pending", "running"]))
    return list((await session.execute(stmt)).scalars().all())


async def count_active_for_host(session: AsyncSession, hostname: str) -> int:
    """Count `running` OR `cancelling` jobs claimed by ANY worker process on
    `hostname`.

    `claimed_by` is `hostname:pid` (see worker `_worker_id`), so the host part
    is `split_part(claimed_by, ':', 1)`. This is the HOST-WIDE busy signal the
    SA-key scrub path needs: an embedded and a standalone worker can share one
    hostname (and thus the same on-disk `active.json` / `.env`), but each only
    sees its own in-process `_tasks`. Before the destructive credential clear,
    the scrub path also checks this so an idle process does not yank shared
    credential files out from under a sibling process that is mid-spawn.
    `cancelling` counts too (renamed from `count_running_for_host`, round 3):
    a job told to stop but not yet unwound is still mid-spawn from the
    credential-file point of view — mirrors the `count_active_for_book`
    in-flight set (pending/running/cancelling) minus `pending` (a pending job
    hasn't claimed this host yet, so it can't be mid-spawn on it).

    Uses `split_part(..) = hostname` (not `LIKE 'hostname:%'`) so a hostname
    containing a LIKE metacharacter can't over- or under-match, and the `:`
    boundary keeps `mac` from matching `mac-mini:123`.
    """
    stmt = select(func.count()).select_from(HomeworkJob).where(
        HomeworkJob.status.in_(["running", "cancelling"]),
        func.split_part(HomeworkJob.claimed_by, ":", 1) == hostname,
    )
    return int((await session.execute(stmt)).scalar_one())


async def latest_by_section(
    session: AsyncSession, book_id: UUID, output_language: Optional[str] = None
) -> dict[UUID, HomeworkJob]:
    """One row per (book, section): the most recent job for that section.

    Uses Postgres' `DISTINCT ON` for a single-pass index scan instead of a
    correlated subquery. Returns an empty dict if the book has no jobs.

    When `output_language` is given the lookup is scoped to jobs of that language,
    so the launcher's per-lesson completion reflects the SELECTED language (a book
    complete in uz is not 'complete' under ru/en). Default `None` preserves the
    all-language aggregate for non-launcher callers (upload/retry/book detail).
    """
    conds = [HomeworkJob.book_id == book_id]
    if output_language is not None:
        conds.append(HomeworkJob.output_language == output_language)
    stmt = (
        select(HomeworkJob)
        .where(*conds)
        .order_by(HomeworkJob.toc_entry_id, HomeworkJob.created_at.desc())
        .distinct(HomeworkJob.toc_entry_id)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {row.toc_entry_id: row for row in rows}


async def list_for_book(session: AsyncSession, book_id: UUID) -> list[HomeworkJob]:
    """Every homework job referencing a book's TOC. `book_id` is set together
    with `toc_entry_id` at create time (see `create`), so a `book_id` filter is
    exactly the set of jobs whose `toc_entry_id` FK would block a
    `delete_for_book` clear-before-insert. Used by the TOC re-extract guard to
    refuse loudly and list the blocking jobs. Index-backed by
    `ix_homework_jobs_book_toc`."""
    stmt = (
        select(HomeworkJob)
        .where(HomeworkJob.book_id == book_id)
        .order_by(HomeworkJob.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def count_by_book_ids(session: AsyncSession, book_ids: list[UUID]) -> dict[UUID, int]:
    """Grouped `COUNT(*)` of homework_jobs per book — ANY status, same
    semantics as `list_for_book`'s /toc/retry blocking guard, just batched:
    ONE query for the whole list instead of one per book (GK2 batch-load
    expectation — backs the Notion availability enrichment route's
    `redo_blocked_by_jobs`). A book with zero referencing jobs is absent from
    the returned mapping — callers default-0 on lookup. Empty input
    short-circuits without touching the session."""
    if not book_ids:
        return {}
    stmt = (
        select(HomeworkJob.book_id, func.count())
        .where(HomeworkJob.book_id.in_(book_ids))
        .group_by(HomeworkJob.book_id)
    )
    rows = (await session.execute(stmt)).all()
    return {book_id: count for book_id, count in rows}


# ─────────────────────────────────────────────────────────────────────────
# Queue (Postgres-backed work queue using FOR UPDATE SKIP LOCKED)
# ─────────────────────────────────────────────────────────────────────────


async def claim_next_job(
    session: AsyncSession,
    *,
    worker_id: str,
    max_attempts: int,
    capabilities: Optional[dict] = None,
    fleet_api_paused: bool = False,
) -> Optional[HomeworkJob]:
    """Atomically claim the next pending job for this worker.

    Uses `FOR UPDATE SKIP LOCKED` so multiple workers polling concurrently
    never collide on the same row — Postgres serializes the dispatch.
    Returns None if no claimable job is available (worker should sleep
    and retry).

    Eligibility rules (claim gate v3 — job-column-based, Task 4):
      - status == 'pending'
      - scheduled_at <= NOW() (so delayed retries don't fire early)
      - attempts < max_attempts (don't reclaim poison-pill jobs forever)
      - per-role capability routing against `capabilities` (the credential-only
        dict `worker._compute_capabilities` builds at startup):
          * content: transport == 'cli', OR the worker has the api capability
            for the job's content provider (`can_claude_api`/`can_gemini_api`).
          * judge: if the job's resolved judge transport is api, gate on the
            STAMPED job.judge_provider — EXCEPT when the job is self-grade
            (content_model_resolved == job.judge_model, both non-NULL), in which
            case the self-fallback peer's credential is required instead.
            `content_model_resolved` is a CASE expression over MODEL_MANIFEST
            that resolves NULL content model to the provider's default, matching
            Python's `resolve_judge` logic exactly.
          * extract: if the job's resolved extract transport is api, gate on
            the STAMPED job.extract_provider's credential.
          * solver: identical rule to judge, mirrored onto the solver_*
            columns — if the job's resolved solver transport is api, gate on
            the STAMPED job.solver_provider, EXCEPT self-solve (content model
            == solver model, both non-NULL) which routes to the self-fallback
            peer's credential instead.
        Default `capabilities=None` is all-False (cli-only).

    Order: highest priority first, then ascending lesson order
    (toc_entries.order_index — so lesson 1 is claimed before lesson 2),
    then oldest scheduled_at first (FIFO within a priority+lesson band).
    """
    from app.services.model_tiers import _PRIMARY_SELF_FALLBACK
    from app.services.agent_models import MODEL_MANIFEST, default_model as _default_model

    caps = capabilities or {}

    content_ok = or_(
        HomeworkJob.transport == "cli",
        and_(HomeworkJob.provider == "claude", literal(bool(caps.get("can_claude_api")))),
        and_(HomeworkJob.provider == "gemini", literal(bool(caps.get("can_gemini_api")))),
        and_(HomeworkJob.provider == "clodex", literal(bool(caps.get("can_clodex_api")))),
    )
    judge_needs_api = or_(
        HomeworkJob.judge_transport == "api",
        and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"),
    )

    def _provider_api_ok(resolved):
        """Map a SQL-expression resolved provider name to the worker's matching cap flag."""
        return or_(
            and_(resolved == "claude", literal(bool(caps.get("can_claude_api")))),
            and_(resolved == "gemini", literal(bool(caps.get("can_gemini_api")))),
            and_(resolved == "clodex", literal(bool(caps.get("can_clodex_api")))),
        )

    # Resolve content model EXACTLY as Python's resolve_judge does
    # (`model or default_model(provider)`) so the SQL self-grade test agrees with
    # the runtime judge decision even when content model is Auto (NULL on cli —
    # the common case). Built from the manifest: no hardcoded model strings, no
    # drift. (We deliberately do NOT stamp content model concrete at launch —
    # that would force gemini-cli Auto-content onto default_model('gemini')=
    # gemini-3.1-pro-preview, a cost regression, since _PROVIDER_DEFAULT_MODEL
    # ['gemini'] is None = "let the CLI pick".)
    content_model_resolved = case(
        *[(and_(HomeworkJob.model.is_(None), HomeworkJob.provider == p), _default_model(p))
          for p in MODEL_MANIFEST],
        else_=func.coalesce(HomeworkJob.model, ""),
    )
    # Self-grade: job's generator == its stamped judge -> judged by the
    # self-fallback peer (claude-opus-4-7, or gemini-3.1-pro-preview when the job
    # IS that primary peer). Gate on the peer's credential for exactly those jobs.
    job_is_self_grade = and_(
        HomeworkJob.provider == HomeworkJob.judge_provider,
        content_model_resolved == func.coalesce(HomeworkJob.judge_model, ""),
    )
    self_grade_judge_provider = case(
        (and_(HomeworkJob.provider == _PRIMARY_SELF_FALLBACK[0],
              content_model_resolved == _PRIMARY_SELF_FALLBACK[1]), "gemini"),
        else_="claude",
    )
    judge_ok = or_(
        not_(judge_needs_api),
        and_(job_is_self_grade, _provider_api_ok(self_grade_judge_provider)),
        and_(not_(job_is_self_grade), _provider_api_ok(HomeworkJob.judge_provider)),
    )
    extract_needs_api = or_(
        HomeworkJob.extract_transport == "api",
        and_(HomeworkJob.extract_transport == "inherit", HomeworkJob.transport == "api"),
    )
    extract_ok = or_(not_(extract_needs_api), _provider_api_ok(HomeworkJob.extract_provider))

    # Solver role (R1 / Task 8): mirrors the judge block above onto the
    # solver_* columns. The solver's api-auth error re-raises (job-level
    # failure) — a job whose resolved solver is claude/api must not be
    # claimed by a worker lacking ANTHROPIC_API_KEY (the all-Vertex fleet).
    solver_needs_api = or_(
        HomeworkJob.solver_transport == "api",
        and_(HomeworkJob.solver_transport == "inherit", HomeworkJob.transport == "api"),
    )
    job_is_self_solve = and_(
        HomeworkJob.provider == HomeworkJob.solver_provider,
        content_model_resolved == func.coalesce(HomeworkJob.solver_model, ""),
    )
    self_solve_provider = case(
        (and_(HomeworkJob.provider == _PRIMARY_SELF_FALLBACK[0],
              content_model_resolved == _PRIMARY_SELF_FALLBACK[1]), "gemini"),
        else_="claude",
    )
    solver_ok = or_(
        not_(solver_needs_api),
        and_(job_is_self_solve, _provider_api_ok(self_solve_provider)),
        and_(not_(job_is_self_solve), _provider_api_ok(HomeworkJob.solver_provider)),
    )

    # Batch-pause gate: skip jobs whose batch is paused.
    # CRITICAL: the IS NULL arm is REQUIRED — without it, `NULL NOT IN
    # (non-empty set)` evaluates to SQL NULL (excluded), so every batchless
    # /generate job (batch_id IS NULL) would become unclaimable the instant any
    # batch is paused. Batchless jobs are never governed by the batch-pause gate.
    not_in_paused_batch = or_(
        HomeworkJob.batch_id.is_(None),
        HomeworkJob.batch_id.not_in(
            select(Batch.id).where(Batch.paused_at.is_not(None))
        ),
    )
    # Fleet-daily global pause gate (Task 5 / C4): when fleet_api_paused=True,
    # skip any job that would spend api tokens (transport='api' OR any resolved
    # role is api). cli-only jobs are never blocked.
    #
    # job_resolved_api = job touches api on ANY role:
    #   transport='api' (content phase is api), OR
    #   judge is api-resolved (judge_transport='api', or 'inherit' under api job), OR
    #   extract is api-resolved (extract_transport='api', or 'inherit' under api job).
    #
    # Reuses the already-computed judge_needs_api / extract_needs_api expressions.
    #
    # Gate logic: or_(~job_resolved_api, literal(not fleet_api_paused))
    #   fleet_api_paused=False → literal(True) → or_ always True → NO-OP (every job passes).
    #   fleet_api_paused=True  → literal(False) → or_ = ~job_resolved_api → only cli jobs pass.
    job_resolved_api = or_(
        HomeworkJob.transport == "api",
        judge_needs_api,
        extract_needs_api,
        solver_needs_api,
    )
    fleet_gate = or_(~job_resolved_api, literal(not fleet_api_paused))

    pick_stmt = (
        select(HomeworkJob.id)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= func.now())
        .where(HomeworkJob.attempts < max_attempts)
        .where(content_ok)
        .where(judge_ok)
        .where(extract_ok)
        .where(solver_ok)
        .where(not_in_paused_batch)
        .where(fleet_gate)
        .order_by(
            HomeworkJob.priority.desc(),
            (
                select(TOCEntry.order_index)
                .where(TOCEntry.id == HomeworkJob.toc_entry_id)
                .scalar_subquery()
            ).asc(),                          # ascending lesson order (NULLS LAST by Postgres default)
            HomeworkJob.scheduled_at.asc(),   # final FIFO tiebreaker
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job_id = (await session.execute(pick_stmt)).scalar_one_or_none()
    if job_id is None:
        return None

    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="running",
            claimed_at=func.now(),
            claimed_by=worker_id,
            attempts=HomeworkJob.attempts + 1,
            last_attempt_at=func.now(),
            started_at=func.now(),
            error_message=None,  # clear stale message from prior attempt
        )
    )
    return await session.get(HomeworkJob, job_id)


async def touch_claim(session: AsyncSession, job_id: UUID) -> None:
    """Heartbeat: refresh claimed_at on a still-running job so the lease-TTL
    reclaim never treats a live worker's job as orphaned. No-ops once the job
    leaves `running` (done/failed), so it can't resurrect a finished row."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(claimed_at=func.now())
    )


async def reclaim_stuck_jobs(
    session: AsyncSession, *, stale_after_seconds: int
) -> int:
    """Promote `running` jobs whose claim is stale back to `pending`.

    Triggered on worker startup (recovers jobs whose worker died mid-run)
    and periodically by the running worker (recovers jobs from peer crashes).
    Returns the number of rows reclaimed.

    Stuck = running and (claimed_at is NULL or claimed_at < now - stale).
    The `attempts` counter persists, so a poison-pill job runs at most
    `max_attempts` times before being marked failed terminally.

    Same-transaction phase reconciliation (orphan-phase-reconciliation-1):
    every reclaimed job's abandoned phase rows are reset to `pending` too, so
    a reclaimed job never shows a stale `running`/orphan-marked phase row.
    """
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "running")
        .where(
            (HomeworkJob.claimed_at.is_(None))
            | (HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        )
        .values(
            status="pending",
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
        )
        .returning(HomeworkJob.id)
    )
    result = await session.execute(stmt)
    reclaimed = [row[0] for row in result.fetchall()]
    if reclaimed:
        # Same-transaction phase reconciliation (orphan-phase-reconciliation-1):
        # a reclaimed job's in-flight rows go back to WAITING. Marker-aware
        # because main.lifespan's boot sweep pre-marks them failed/"orphaned:
        # worker restarted" before the startup reclaim runs.
        await phase_repo.reset_abandoned_phases(
            session, reclaimed,
            status="pending",
            source_statuses=("running",),
            include_orphan_failed=True,
        )
    return len(reclaimed)


async def reclaim_orphans_on_startup(
    session: AsyncSession, *, reclaim_stale_seconds: int
) -> int:
    """Peer-aware startup reclaim: reset `running` jobs to `pending` on boot,
    but only yank a job if no live peer could own it.

    When another worker's heartbeat is fresh (within `reclaim_stale_seconds`),
    that peer may be mid-run on a recently-claimed job — using window=0 would
    reset it and cause a double-run (real $ for api jobs). In that case we use
    the full lease window so any job whose claim is still fresh is left alone.

    When no live peer exists (solo restart / first boot), every `running` row
    is genuinely orphaned → window=0 resets them all immediately (preserves
    instant single-host recovery).

    Best-effort caveat: on a sub-`reclaim_stale_seconds` restart, the prior
    process's own row (same host, old pid) may still have a fresh heartbeat and
    read as a live peer → lease path fires, so instant reset doesn't happen.
    Correctness is unaffected (the old row's claim will expire naturally);
    instant recovery just doesn't fire for that narrow window.

    Does NOT filter by hostname — two processes on one host are legitimately
    distinct peers; hostname filtering would risk a same-host double-run.
    """
    if await workers_repo.has_live_workers(session, stale_after_seconds=reclaim_stale_seconds):
        window = reclaim_stale_seconds
    else:
        window = 0
    return await reclaim_stuck_jobs(session, stale_after_seconds=window)


async def fail_exhausted_pending_jobs(session: AsyncSession, *, max_attempts: int) -> int:
    """Mark `pending` jobs whose attempts are exhausted as terminally failed.

    Such rows are skipped by the claim query (attempts >= max_attempts) yet
    never failed (mark_failed_with_retry only runs for claimed jobs), so
    without this sweep they wedge in `pending` forever. Returns rows failed.

    Same-transaction phase reconciliation (orphan-phase-reconciliation-1):
    the job is terminal, so every unfinished phase row is failed too — makes
    the failure VISIBLE at phase level instead of a job failing silently with
    zero failed phase rows.
    """
    _msg = "attempts exhausted while pending (stale-pending sweep)"
    stmt = (
        update(HomeworkJob)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.attempts >= max_attempts)
        .values(
            status="failed",
            completed_at=func.now(),
            error_message=_msg,
            last_error=_msg,
            claimed_at=None,
            claimed_by=None,
        )
        .returning(HomeworkJob.id)
    )
    result = await session.execute(stmt)
    failed_ids = [row[0] for row in result.fetchall()]
    if failed_ids:
        # Terminal job ⇒ every unfinished row terminal too (mirrors
        # mark_cancelled) — makes the failure VISIBLE at phase level: the
        # 10-done+1-running field case previously failed with zero failed
        # phase rows, invisible to failed/cancelled-based watchers.
        await phase_repo.reset_abandoned_phases(
            session, failed_ids,
            status="failed",
            error_message=_msg,
            source_statuses=("pending", "running"),
            include_orphan_failed=True,
        )
    return len(failed_ids)


async def mark_failed_with_retry(
    session: AsyncSession,
    job_id: UUID,
    *,
    error_message: str,
    max_attempts: int,
    backoff_seconds: int = 30,
) -> str:
    """Record a failed attempt. Either re-schedules with exponential backoff
    (status='pending', scheduled_at in the future) or marks terminal failure
    (status='failed') if attempts exhausted.

    Returns the resulting status ('pending' = will retry, 'failed' = terminal).
    """
    job = await session.get(HomeworkJob, job_id)
    if job is None:
        return "missing"

    if job.status == "cancelling":
        return await _finalize_if_cancelling(session, job_id)

    if job.attempts >= max_attempts:
        # Terminal: stay in failed, store the error.
        result = await session.execute(
            update(HomeworkJob)
            .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
            .values(
                status="failed",
                completed_at=func.now(),
                error_message=error_message,
                last_error=error_message,
                claimed_at=None,
                claimed_by=None,
            )
        )
        if result.rowcount == 0:
            return await _finalize_if_cancelling(session, job_id)
        return "failed"

    # Retry: bump scheduled_at by exponential backoff (30s, 60s, 120s, ...).
    delay = backoff_seconds * (2 ** (job.attempts - 1))
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
        .values(
            status="pending",
            scheduled_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, delay),
            last_error=error_message,
            current_phase=None,
            claimed_at=None,
            claimed_by=None,
        )
    )
    if result.rowcount == 0:
        return await _finalize_if_cancelling(session, job_id)
    return "pending"


async def requeue_session_limited(
    session: AsyncSession,
    job_id: UUID,
    *,
    error: str,
) -> str:
    """Requeue a session-limited job without burning a retry attempt.

    Sets status='pending', decrements attempts by 1 (GREATEST to floor at 0),
    clears claim columns, and sets scheduled_at=NOW() so a healthy peer can
    claim it immediately. Does NOT mark the job failed — it will be retried
    once the session limit resets.

    Host-clock note: scheduled_at uses DB clock (func.now()) for consistency
    with all other queue timestamps (fleet-net-1 ops half).

    Guarded on status='running' (gate correction 6, same sibling pattern as
    requeue_slot_saturated): a concurrent user cancel must win — resurrecting
    a 'cancelling' job to 'pending' would let it run again after the user
    asked to stop it. Returns "requeued", "cancelled", or "skipped".
    """
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
        .values(
            status="pending",
            attempts=func.greatest(HomeworkJob.attempts - 1, 0),
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
            last_error=error,
            scheduled_at=func.now(),
        )
    )
    if result.rowcount == 0:
        return await _finalize_if_cancelling(session, job_id)
    return "requeued"


async def _finalize_if_cancelling(session: AsyncSession, job_id: UUID) -> str:
    """Cancel-wins helper (gate correction 6): when a guarded requeue/retry
    UPDATE matched 0 rows, the job's status changed under us. The caller may
    already hold this job in the session's identity map (mark_failed_with_retry
    loads it at entry), so `session.get` would return the STALE pre-cancel
    object (the BE-02 expire-before-re-fetch lesson) — re-read the status as
    a fresh column scalar instead. If a user cancel won, finalize via the
    existing mark_cancelled semantics (job -> cancelled AND every non-done
    phase row -> failed). A stopped job must never resurrect to pending."""
    status = await session.scalar(
        select(HomeworkJob.status)
        .where(HomeworkJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    if status is None:
        return "skipped"
    if status == "cancelling":
        await mark_cancelled(session, job_id)   # jobs.py:796 — job + phase rows
        return "cancelled"
    return "skipped"


async def requeue_slot_saturated(
    session: AsyncSession,
    job_id: UUID,
    *,
    error: str,
    cooldown_seconds: int,
) -> str:
    """Park a job whose api call exhausted the fleet credential-slot wait.

    Like requeue_session_limited: attempt refunded (claim's increment is
    compensated), claim cleared, NOT failed. Unlike it: scheduled_at is
    pushed cooldown_seconds into the future (DB clock) so the fleet backs
    off the saturated credential instead of thrashing re-claims.

    Guarded on status='running' (gate correction 6): a concurrent cancel
    must win — returns "parked", "cancelled", or "skipped"."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
        .values(
            status="pending",
            attempts=func.greatest(HomeworkJob.attempts - 1, 0),
            claimed_at=None,
            claimed_by=None,
            current_phase=None,
            last_error=error,
            scheduled_at=func.now()
            + func.make_interval(0, 0, 0, 0, 0, 0, cooldown_seconds),
        )
    )
    if result.rowcount > 0:
        return "parked"
    return await _finalize_if_cancelling(session, job_id)


async def queue_depth(session: AsyncSession) -> int:
    """Count of pending jobs eligible to run right now. Used by the
    `/generate` endpoint to enforce backpressure.

    Mirrors the claim gate's batch-pause predicate: a paused batch's pending
    jobs are dormant (unclaimable by design) and must not fill the
    backpressure limit — 57 paused jobs once 503'd an unrelated 1-job enqueue.
    LEFT JOIN keeps batchless jobs counted (no Batch row → paused_at NULL),
    matching the claim gate's batch_id-IS-NULL arm."""
    stmt = (
        select(func.count())
        .select_from(HomeworkJob)
        .outerjoin(Batch, HomeworkJob.batch_id == Batch.id)
        .where(HomeworkJob.status == "pending")
        .where(HomeworkJob.scheduled_at <= func.now())
        .where(Batch.paused_at.is_(None))
    )
    return int((await session.execute(stmt)).scalar_one())


# ─────────────────────────────────────────────────────────────────────────
# Cancellation
# ─────────────────────────────────────────────────────────────────────────


async def cancel_if_pending(session: AsyncSession, job_id: UUID) -> bool:
    """Atomically cancel a still-queued job. Returns True iff it transitioned
    pending->cancelled (so the worker can never have claimed it). False means
    it was already claimed/running/done — caller falls through to request_cancel."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "pending")
        .values(status="cancelled", completed_at=func.now())
    )
    return result.rowcount > 0


async def request_cancel(session: AsyncSession, job_id: UUID) -> bool:
    """Signal cancel for a RUNNING job: running->cancelling. Returns True iff it
    transitioned (the owning worker / same-process registry then cancels the
    task and finalizes). False means it wasn't running (done/failed/etc)."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .where(HomeworkJob.status == "running")
        .values(status="cancelling")
    )
    return result.rowcount > 0


async def get_status(session: AsyncSession, job_id: UUID) -> Optional[str]:
    """Lightweight status read (used by the heartbeat to notice a cancel)."""
    return (
        await session.execute(
            select(HomeworkJob.status).where(HomeworkJob.id == job_id)
        )
    ).scalar_one_or_none()


async def mark_cancelled(session: AsyncSession, job_id: UUID) -> None:
    """Finalize a user-cancelled job: job -> cancelled; any non-done phase rows
    -> failed (they were interrupted/killed). DONE phases are preserved so a
    later /retry can resume (worklog 0031)."""
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(status="cancelled", completed_at=func.now())
    )
    await session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == job_id)
        .where(PhaseOutput.status != "done")
        .values(status="failed")
    )


async def cancel_all_in_batch(session: AsyncSession, batch_id: UUID) -> dict[str, int]:
    """Cancel every non-terminal job in a batch in one transaction: pending ->
    cancelled (never claimed), running -> cancelling (the worker/heartbeat then
    kills the task). done/failed/cancelled/cancelling are left untouched.
    Returns {"cancelled": n_pending, "cancelling": n_running}."""
    pend = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.batch_id == batch_id, HomeworkJob.status == "pending")
        .values(status="cancelled", completed_at=func.now()))
    run = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.batch_id == batch_id, HomeworkJob.status == "running")
        .values(status="cancelling"))
    return {"cancelled": pend.rowcount, "cancelling": run.rowcount}


async def count_active_for_book(session: AsyncSession, book_id: UUID) -> int:
    """Count jobs still in flight (pending/running/cancelling) for a book —
    used by the book-delete guard (BE-02 task 2) to refuse deletion while a
    job could still be spawning against files the delete would remove."""
    stmt = (
        select(func.count())
        .select_from(HomeworkJob)
        .where(HomeworkJob.book_id == book_id,
               HomeworkJob.status.in_(["pending", "running", "cancelling"]))
    )
    return (await session.execute(stmt)).scalar_one()


async def running_job_ids_in_batch(session: AsyncSession, batch_id: UUID) -> list[UUID]:
    """Job ids that were `cancelling` after cancel_all — so the API can cancel any
    locally-running tasks instantly (rather than waiting for the heartbeat)."""
    rows = await session.execute(
        select(HomeworkJob.id).where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status == "cancelling"))
    return list(rows.scalars().all())


async def resume_failed_in_batch(session: AsyncSession, batch_id: UUID) -> dict:
    """Re-enqueue every failed/cancelled job in a batch via reset_for_retry
    (status->pending, attempts->0). reset_for_retry keeps phase rows, so the
    pipeline RESUMES — done phases are reused, only unfinished ones re-run.

    SKIPS any job pinned to a retired model (gemini-2.5, retired 2026-08-03,
    see job_reactivation.retired_models_in_job) instead of resuming it — resume
    reuses the job's pinned provider/model verbatim, so re-enqueuing a
    retired-stamped job would call a dead model.

    Returns ``{"resumed": <count re-enqueued>, "skipped_retired": [<job id
    str>, ...]}``.
    """
    from app.services import job_reactivation

    rows = await session.execute(
        select(HomeworkJob).where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status.in_(["failed", "cancelled"])))
    jobs = list(rows.scalars().all())
    resumed = 0
    skipped_retired: list[str] = []
    for job in jobs:
        if job_reactivation.retired_models_in_job(job):
            skipped_retired.append(str(job.id))
            continue
        await reset_for_retry(session, job.id)
        resumed += 1
    return {"resumed": resumed, "skipped_retired": skipped_retired}


async def latest_for_section(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID, *,
    transport: Optional[str] = None,
    output_language: str,
) -> Optional[HomeworkJob]:
    """The most recent job for a (book, section, output_language) regardless of
    status — used by relaunch to find a failed/cancelled job to RESUME rather
    than recreate.

    `output_language` scopes the lookup so an EN relaunch over a previously-
    failed UZ section finds the EN job (not the UZ one) and resumes it correctly
    instead of creating a new EN job that would duplicate work.
    """
    conds = [HomeworkJob.book_id == book_id,
             HomeworkJob.toc_entry_id == toc_entry_id,
             HomeworkJob.output_language == output_language]
    if transport is not None:
        conds.append(HomeworkJob.transport == transport)
    stmt = (select(HomeworkJob).where(*conds)
            .order_by(HomeworkJob.created_at.desc()).limit(1))
    return (await session.execute(stmt)).scalar_one_or_none()


async def done_phase_count_for_job(session: AsyncSession, job_id: UUID) -> int:
    """How many `done` phase rows with non-empty output a job has — the 'saved
    work' a relaunch would discard if it recreated the job."""
    from app.models.phase_output import PhaseOutput
    stmt = select(func.count()).select_from(PhaseOutput).where(
        PhaseOutput.job_id == job_id,
        PhaseOutput.status == "done",
        func.coalesce(PhaseOutput.output_md, "") != "")
    return int((await session.execute(stmt)).scalar_one())


async def reclaim_stale_cancelling(
    session: AsyncSession, stale_after_seconds: int
) -> int:
    """Finalize jobs stuck in `cancelling` whose claim is older than the lease
    window — i.e. the owning worker crashed mid-cancel. They're excluded from
    both claim (pending) and reclaim (running) sweeps, so without this they'd
    hang forever. The intent was to cancel, so -> cancelled."""
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.status == "cancelling")
        .where(HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        .values(status="cancelled", completed_at=func.now())
    )
    return result.rowcount or 0
