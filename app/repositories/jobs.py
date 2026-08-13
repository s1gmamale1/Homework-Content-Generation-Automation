from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, case, exists, func, literal, not_, or_, select, text, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Batch, HomeworkJob, PhaseOutput, TOCEntry, WorkerNode
from app.repositories import lease_events
from app.repositories import phase_outputs as phase_repo
from app.repositories import workers as workers_repo
from app.services import lease

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
    start_offset_seconds: int = 0,
    kind: str = "homework",
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
        kind=kind,
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
    # Launch stagger (plan 2026-08-11): a positive offset pushes scheduled_at
    # into the future so the claim gate (`scheduled_at <= func.now()`,
    # claim_next_job) holds this job back until its wave is due. DB clock, never
    # the host clock — the gate compares against func.now() and worker host
    # clocks drift (same reasoning as the host-clock note on
    # mark_failed_with_retry). Left unset at offset 0 so the column keeps its
    # NOW() server default and every pre-existing caller is untouched.
    if start_offset_seconds > 0:
        kwargs["scheduled_at"] = func.now() + func.make_interval(
            0, 0, 0, 0, 0, 0, start_offset_seconds)
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
    kind: str = "homework",
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

    `kind` scopes the lookup so a 'teacher_material' launch never adopts a
    'homework' job (and vice versa). Default 'homework' keeps every existing
    caller behavior-identical.
    """
    conds = [
        HomeworkJob.book_id == book_id,
        HomeworkJob.toc_entry_id == toc_entry_id,
        HomeworkJob.status.in_(["pending", "running", "done"]),
        HomeworkJob.output_language == output_language,
        HomeworkJob.kind == kind,
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
    claim_token: Optional[UUID] = None,
) -> object:
    """Set a job's status. With ``guard`` (default), a guarded UPDATE refuses to
    overwrite a terminal status or resurrect a `cancelling` job (cancel-race-1):
    terminal {done,failed,cancelled} is frozen; from `cancelling` only
    `cancelled` is allowed. Mirrors ``status_write_allowed``. Returns True iff a
    row was updated. claim (pending->running) and reset_for_retry
    (cancelled->pending) use their OWN updates and are unaffected.

    Transitional (fenced job leases, Task 5): with ``claim_token=None`` (every
    caller until Tasks 6-7) the legacy behavior is preserved UNCHANGED and a
    ``bool`` is returned. When a token is given, the write is fenced through
    ``_fenced_update`` — the job id on success, ``lease.LeaseLost`` /
    ``lease.CancelRequested`` when the lease no longer owns the row (a `done`
    transition emits ``EVENT_RELEASED_DONE``)."""
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = started_at
    if completed_at is not None:
        values["completed_at"] = completed_at
    if error_message is not None:
        values["error_message"] = error_message
    if current_phase is not None:
        values["current_phase"] = current_phase
    if claim_token is not None:
        if guard:
            guards = [HomeworkJob.status.not_in(_TERMINAL_STATUSES)]
            if status != "cancelled":
                guards.append(HomeworkJob.status != "cancelling")
            status_guard = and_(*guards)
        else:
            status_guard = true()
        return await _fenced_update(
            session,
            job_id,
            claim_token,
            values,
            status_guard=status_guard,
            release_event=lease.EVENT_RELEASED_DONE if status == "done" else None,
        )
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
    session: AsyncSession, job_id: UUID, batch_id: Optional[UUID] = None,
    *, start_offset_seconds: int = 0,
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

    ``start_offset_seconds``: launch stagger (plan 2026-08-11). This function
    deliberately did NOT touch ``scheduled_at`` before, so a resumed job kept its
    original (past) timestamp and became claimable the instant it flipped to
    pending — which reproduces the same synchronised burst a fresh batch launch
    does. A positive offset pushes it out on the DB clock. Default 0 keeps the
    historical behaviour byte-for-byte for every other caller.

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
    # A manual retry is a fresh start for BOTH budgets — a job that previously
    # hit the reclaim ceiling must get its refunds back, or the operator retry
    # would burn straight through `attempts` again (retry-accounting-1).
    job.reclaims = 0
    # Rotate the lease: a retried-from-failed job must not keep a dead claim
    # token or stale claim columns (fenced job leases, Task 4).
    job.claim_token = None
    job.claimed_at = None
    job.claimed_by = None
    if start_offset_seconds > 0:
        job.scheduled_at = func.now() + func.make_interval(
            0, 0, 0, 0, 0, 0, start_offset_seconds)
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
    session: AsyncSession, book_id: UUID, output_language: Optional[str] = None,
    *, kind: str = "homework",
) -> dict[UUID, HomeworkJob]:
    """One row per (book, section): the most recent job for that section.

    Uses Postgres' `DISTINCT ON` for a single-pass index scan instead of a
    correlated subquery. Returns an empty dict if the book has no jobs.

    When `output_language` is given the lookup is scoped to jobs of that language,
    so the launcher's per-lesson completion reflects the SELECTED language (a book
    complete in uz is not 'complete' under ru/en). Default `None` preserves the
    all-language aggregate for non-launcher callers (upload/retry/book detail).

    `kind` scopes the lookup so a 'teacher_material' job never displays as a
    section's "latest" on the homework TOC-status enrichment (Task 9, mirrors
    `find_active_for_section`/`latest_for_section`). Default 'homework' keeps
    every existing caller behavior-identical.
    """
    conds = [HomeworkJob.book_id == book_id, HomeworkJob.kind == kind]
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
) -> Optional[lease.ClaimedJob]:
    """Atomically claim the next pending job for this worker.

    Uses `FOR UPDATE SKIP LOCKED` so multiple workers polling concurrently
    never collide on the same row — Postgres serializes the dispatch.
    Mints a fresh per-claim `claim_token`, stamps it on the job row, and
    records a `claimed` ledger event in the same transaction (fenced job
    leases, Task 3). Returns a `lease.ClaimedJob(job, lease)` pairing the
    claimed row with its `lease.JobLease`, or None if no claimable job is
    available (worker should sleep and retry).

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

    token = uuid4()
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id)
        .values(
            status="running",
            claimed_at=func.now(),
            claimed_by=worker_id,
            claim_token=token,
            attempts=HomeworkJob.attempts + 1,
            last_attempt_at=func.now(),
            started_at=func.now(),
            error_message=None,  # clear stale message from prior attempt
        )
    )
    await lease_events.append_event(
        session,
        job_id=job_id,
        claim_token=token,
        event_type=lease.EVENT_CLAIMED,
        owner=worker_id,
    )
    job = await session.get(HomeworkJob, job_id)
    return lease.ClaimedJob(
        job=job,
        lease=lease.JobLease(job_id=job_id, claim_token=token, owner_id=worker_id),
    )


async def finalize_cancelled(
    session: AsyncSession, job_id: UUID, claim_token: UUID
) -> object:
    """Fenced `cancelling`->`cancelled` that ALSO fails every non-done phase row
    (the shipped 0155 cancel contract — mirrors ``mark_cancelled``'s second
    UPDATE) and clears the lease. This is the NEW fenced path the *worker* uses
    to complete the flip that the admin/operator ``mark_cancelled`` used to do.

    Single-finalize contract (fenced job leases, Task 5): ``_fenced_update``
    calls this itself on same-token + `cancelling`, so the worker treats a
    returned ``CancelRequested`` as a pure signal and never finalizes again.
    Therefore this MUST be idempotent-silent: if the job is already `cancelled`
    it returns ``lease.CancelRequested`` without a duplicate event or a spurious
    lease-loss. Returns ``lease.LeaseLost`` if the token no longer matches."""
    row = await session.get(HomeworkJob, job_id, populate_existing=True)
    if row is None or row.status == "cancelled":
        return lease.CancelRequested
    if row.claim_token != claim_token:
        return lease.LeaseLost
    await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "cancelling")
        .values(
            status="cancelled",
            completed_at=func.now(),
            claim_token=None,
            claimed_at=None,
            claimed_by=None,
        )
    )
    # Phase sweep — every non-done phase row is failed (the interrupted work),
    # done phases preserved so a later /retry can resume (worklog 0031/0155).
    await session.execute(
        update(PhaseOutput)
        .where(PhaseOutput.job_id == job_id, PhaseOutput.status != "done")
        .values(status="failed", completed_at=func.now(), claim_token=None)
    )
    await lease_events.append_event(
        session,
        job_id=job_id,
        claim_token=claim_token,
        event_type=lease.EVENT_RELEASED_CANCELLED,
    )
    return lease.CancelRequested


async def _fenced_update(
    session: AsyncSession,
    job_id: UUID,
    claim_token: UUID,
    values: dict,
    *,
    status_guard,
    release_event: Optional[str] = None,
    finalize_on_cancel: bool = True,
) -> object:
    """Token-fenced worker write (fenced job leases, Task 5). Applies ``values``
    only when the row still carries ``claim_token`` AND satisfies
    ``status_guard``. On a hit it optionally appends ``release_event`` and
    returns the job id.

    On a 0-row match it re-reads (never guesses) to distinguish the two ways a
    fenced write can miss:
      * token gone/changed -> the lease was lost (returns ``lease.LeaseLost``,
        ledgered as ``lease_lost``);
      * same token + status='cancelling' -> a user cancel won. With
        ``finalize_on_cancel=True`` (the default, for TERMINAL writes —
        ``set_status``/``mark_failed_with_retry``/``requeue_*``) this finalizes
        via ``finalize_cancelled`` (single-finalize contract) and returns
        ``lease.CancelRequested``. With ``finalize_on_cancel=False`` (the
        heartbeat refresh) it returns ``lease.CancelRequested`` as a PURE SIGNAL
        — it does NOT finalize and does NOT mutate the row (a heartbeat must
        never finalize; only the worker's terminal write does);
      * same token + some other terminal status -> ``lease.LeaseLost``.

    A DB/connectivity error propagates — it is NOT swallowed into a lease-loss.
    """
    stmt = (
        update(HomeworkJob)
        .where(
            HomeworkJob.id == job_id,
            HomeworkJob.claim_token == claim_token,
            status_guard,
        )
        .values(**values)
        .returning(HomeworkJob.id)
    )
    hit = (await session.execute(stmt)).scalar_one_or_none()
    if hit is not None:
        if release_event:
            await lease_events.append_event(
                session,
                job_id=job_id,
                claim_token=claim_token,
                event_type=release_event,
            )
        return hit
    row = await session.get(HomeworkJob, job_id, populate_existing=True)
    if row is None or row.claim_token != claim_token:
        await lease_events.append_event(
            session,
            job_id=job_id,
            claim_token=claim_token,
            event_type=lease.EVENT_LEASE_LOST,
        )
        return lease.LeaseLost
    if row.status == "cancelling":
        if finalize_on_cancel:
            return await finalize_cancelled(session, job_id, claim_token)
        return lease.CancelRequested  # pure signal — heartbeat must not finalize
    return lease.LeaseLost  # token matches but status moved terminal underneath us


async def heartbeat_check(
    session: AsyncSession, job_id: UUID, claim_token: UUID
) -> lease.HeartbeatOutcome:
    """Classify a running job's lease for the worker heartbeat (fenced job
    leases, Task 5). Re-reads status+token:
      * row gone -> ``HeartbeatOutcome.LOST``;
      * token gone/changed -> ``HeartbeatOutcome.LOST`` (reclaimed under us, or a
        peer took over and finished/cleared it) — checked FIRST;
      * status terminal (done/failed/cancelled) WITH our token ->
        ``HeartbeatOutcome.FINISHED`` — the worker finished its OWN job (a
        terminal transition by the owner keeps the token) so the heartbeat must
        STOP without cancelling its post-done work (D1). A terminal row under a
        foreign/cleared token already returned LOST above;
      * status='cancelling' -> ``HeartbeatOutcome.CANCELLING`` (user cancel);
      * otherwise refresh the claim (``touch_claim``) and ``RENEWED``.
    It NEVER finalizes — the worker's normal terminal write finalizes; the
    heartbeat only signals. The renew path inspects the fenced ``touch_claim``
    result so a cancel that commits in the refresh window (READ COMMITTED:
    between this re-read and the UPDATE) is reported as CANCELLING, not a false
    RENEWED — ``touch_claim`` runs with ``finalize_on_cancel=False`` so that
    race can never finalize the job here. A DB/connectivity error propagates (it
    is NOT swallowed into ``LOST``)."""
    row = await session.get(HomeworkJob, job_id, populate_existing=True)
    # Token check FIRST, terminal SECOND. A terminal row under a FOREIGN or
    # cleared token means a peer finished/reclaimed the job — we lost the lease
    # and must be cancelled (LOST), never reported FINISHED. FINISHED is only for
    # OUR own terminal job (token still matches). This relies on a terminal
    # transition by the owner NOT clearing the token — see the note at
    # mark_failed_with_retry / the pipeline `done`-write. Ordering these the
    # other way returns FINISHED for a peer's terminal job and leaves this worker
    # running on a job it no longer owns (D1, gate re-review).
    if row is None or row.claim_token != claim_token:
        return lease.HeartbeatOutcome.LOST
    if row.status in _TERMINAL_STATUSES:
        return lease.HeartbeatOutcome.FINISHED
    if row.status == "cancelling":
        return lease.HeartbeatOutcome.CANCELLING
    outcome = await touch_claim(session, job_id, claim_token=claim_token)
    if outcome is lease.CancelRequested:
        return lease.HeartbeatOutcome.CANCELLING
    if outcome is lease.LeaseLost:
        return lease.HeartbeatOutcome.LOST
    return lease.HeartbeatOutcome.RENEWED


async def touch_claim(
    session: AsyncSession, job_id: UUID, claim_token: Optional[UUID] = None
) -> object:
    """Heartbeat: refresh claimed_at on a still-running job so the lease-TTL
    reclaim never treats a live worker's job as orphaned. No-ops once the job
    leaves `running` (done/failed), so it can't resurrect a finished row.

    Transitional (fenced job leases, Task 5): with ``claim_token=None`` the
    legacy behavior is preserved UNCHANGED (no token predicate). When a token is
    given, the refresh is fenced with ``finalize_on_cancel=False`` — a heartbeat
    MUST NOT finalize a cancel-winning job (only the worker's terminal write
    does); a cancel that lands in the refresh window returns
    ``lease.CancelRequested`` as a pure signal, LeaseLost when the lease is
    gone."""
    if claim_token is None:
        await session.execute(
            update(HomeworkJob)
            .where(HomeworkJob.id == job_id)
            .where(HomeworkJob.status == "running")
            .values(claimed_at=func.now())
        )
        return None
    return await _fenced_update(
        session,
        job_id,
        claim_token,
        {"claimed_at": func.now()},
        status_guard=(HomeworkJob.status == "running"),
        finalize_on_cancel=False,
    )


async def reclaim_stuck_jobs(
    session: AsyncSession,
    *,
    stale_after_seconds: int,
    registry_stale_seconds: Optional[int] = None,
    job_timeout_seconds: Optional[int] = None,
    max_reclaims: Optional[int] = None,
) -> int:
    """Promote `running` jobs whose claim is stale back to `pending`, ROTATING
    (clearing) the lease `claim_token` in the same UPDATE (fenced job leases,
    Task 4). Returns the number of rows reclaimed.

    Triggered on worker startup (recovers jobs whose worker died mid-run)
    and periodically by the running worker (recovers jobs from peer crashes).

    Two matched sets, both fencing-aware:

      NORMAL (stale): `running` AND (claimed_at is NULL OR claimed_at < now -
      stale) AND the owning process is NOT live in the `workers` registry
      (no `workers` row for `claimed_by` with a heartbeat within
      `registry_stale_seconds`). A live owner blocks normal reclaim even when
      claimed_at looks stale — the heartbeat, not just the claim age, is the
      liveness signal. Event: `reclaimed_stale`.

      FORCED (hard deadline): `running` AND started_at < now -
      (`job_timeout_seconds` + stale). This ignores a live owner — past the
      hard deadline a job is yanked regardless of heartbeat, so a wedged-but-
      heartbeating worker can never pin a job forever. Event: `reclaimed_forced`.

    Each reclaimed job's ledger event carries the OLD (pre-rotation) token,
    captured by a `FOR UPDATE SKIP LOCKED` snapshot taken BEFORE the nulling
    UPDATE — a plain `RETURNING claim_token` would return the NEW (NULL) value.

    Attempt accounting (retry-accounting-1). `attempts` is charged at CLAIM
    time by `claim_next_job`, so a reclaim that happens before the job ever
    started a phase would charge the retry budget for a SCHEDULING failure —
    a worker blocking on a contended lock, a peer dying between claim and
    first phase. That budget exists to bound EXECUTION failures. Each
    reclaimed job is therefore partitioned by execution evidence:

      * EXECUTED — `attempts` persists (unchanged behaviour), so a poison-pill
        job still runs at most `max_attempts` times before being marked failed
        terminally. `reclaims` resets to 0.
      * NEVER EXECUTED, under the `max_reclaims` ceiling — the claim's
        `attempts` increment is REFUNDED (`GREATEST(attempts - 1, 0)`, the
        same idiom as `requeue_session_limited` / `requeue_slot_saturated`)
        and `reclaims` is bumped instead. Queued work survives contention.
      * NEVER EXECUTED, at the ceiling — refund stops. A job that is claimed
        and reclaimed forever without ever starting a phase is genuinely
        wedged, so it falls back to burning `attempts` and terminates through
        the existing `fail_exhausted_pending_jobs` path rather than
        free-requeueing (and re-occupying a worker slot) indefinitely.

    Same-transaction phase reconciliation (orphan-phase-reconciliation-1):
    every reclaimed job's abandoned phase rows are reset to `pending` too, so
    a reclaimed job never shows a stale `running`/orphan-marked phase row.
    """
    if registry_stale_seconds is None:
        registry_stale_seconds = settings.worker_registry_stale_seconds
    if job_timeout_seconds is None:
        job_timeout_seconds = settings.job_timeout_seconds
    if max_reclaims is None:
        max_reclaims = settings.queue_max_reclaims

    # Owner is live iff a workers row for this job's claimed_by heartbeat-ed
    # within the registry window — the registry-liveness cross-check.
    owner_live = exists(
        select(WorkerNode.pc_id).where(
            WorkerNode.pc_id == HomeworkJob.claimed_by,
            WorkerNode.last_heartbeat
            >= func.now() - func.make_interval(0, 0, 0, 0, 0, 0, registry_stale_seconds),
        )
    )

    # Did THIS claim actually begin executing? (retry-accounting-1.) Both
    # writes below are made by `pipeline._execute_phase` (and its teacher-deck
    # twin) in ONE committed transaction at the top of the FIRST phase, so
    # they flip together and neither can be observed without the other:
    #   * homework_jobs.current_phase = <phase>   (jobs_repo.set_status, fenced)
    #   * phase_outputs.claim_token   = <lease>   (create_or_reset(lease=...))
    # Both are per-CLAIM, not per-job-history: every requeue-to-pending path
    # NULLs `current_phase` (this sweep, mark_failed_with_retry,
    # requeue_session_limited, requeue_slot_saturated, reset_for_retry), and a
    # phase row only matches once `claim_token` has been re-stamped with the
    # CURRENT lease (`reset_abandoned_phases` clears it on every requeue, and
    # frozen `done` rows keep the OLD token, which cannot match).
    # They are ORed, not ANDed, so a refund requires the ABSENCE of all
    # evidence — the conservative direction: mis-charging a stuck job costs one
    # retry, mis-refunding an executing one risks an unbounded re-run loop.
    executed = or_(
        HomeworkJob.current_phase.is_not(None),
        exists(
            select(PhaseOutput.id).where(
                PhaseOutput.job_id == HomeworkJob.id,
                PhaseOutput.claim_token == HomeworkJob.claim_token,
            )
        ),
    )

    # NORMAL/stale snapshot — capture (id, OLD token, execution evidence,
    # reclaim streak) under a row lock BEFORE nulling. The evidence MUST be
    # read here: the reclaim UPDATE below NULLs `current_phase` and
    # `reset_abandoned_phases` clears the phase tokens, destroying both signals.
    # SKIP LOCKED so concurrent sweepers never collide on a row.
    stale_snapshot = (
        select(HomeworkJob.id, HomeworkJob.claim_token, executed, HomeworkJob.reclaims)
        .where(HomeworkJob.status == "running")
        .where(
            (HomeworkJob.claimed_at.is_(None))
            | (HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds))
        )
        .where(~owner_live)
        .with_for_update(skip_locked=True)
    )
    stale_rows = (await session.execute(stale_snapshot)).all()
    stale_ids = [row[0] for row in stale_rows]

    # FORCED snapshot — past the hard deadline, ignore a live owner. Exclude
    # rows already claimed by the stale set so a job can't be double-counted /
    # double-evented (the same-txn row lock would otherwise re-return them).
    forced_conds = [
        HomeworkJob.status == "running",
        HomeworkJob.started_at
        < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, job_timeout_seconds + stale_after_seconds),
    ]
    if stale_ids:
        forced_conds.append(HomeworkJob.id.not_in(stale_ids))
    forced_snapshot = (
        select(HomeworkJob.id, HomeworkJob.claim_token, executed, HomeworkJob.reclaims)
        .where(*forced_conds)
        .with_for_update(skip_locked=True)
    )
    forced_rows = (await session.execute(forced_snapshot)).all()
    forced_ids = [row[0] for row in forced_rows]

    reclaimed = stale_ids + forced_ids
    if reclaimed:
        base_values = dict(
            status="pending",
            claimed_at=None,
            claimed_by=None,
            claim_token=None,
            current_phase=None,
        )
        # Partition by execution evidence (retry-accounting-1) — see the
        # docstring. Every group shares the same requeue values and adds only
        # the counter columns that differ; empty groups emit no UPDATE at all,
        # so the common single-group sweep is still a single statement.
        executed_ids: list = []
        free_ids: list = []
        capped_ids: list = []
        for _id, _tok, did_execute, reclaims in stale_rows + forced_rows:
            if did_execute:
                executed_ids.append(_id)
            elif reclaims < max_reclaims:
                free_ids.append(_id)
            else:
                capped_ids.append(_id)
        for ids, extra in (
            # Ran and failed: the attempt was real — charge it, and clear the
            # scheduling-failure streak.
            (executed_ids, {"reclaims": 0}),
            # Never started: refund the claim's increment, count the reclaim.
            (free_ids, {
                "attempts": func.greatest(HomeworkJob.attempts - 1, 0),
                "reclaims": HomeworkJob.reclaims + 1,
            }),
            # Streak budget spent: stop refunding so `attempts` can terminate a
            # genuinely wedged job. `reclaims` is deliberately NOT reset here —
            # resetting would re-open the refund and loop forever.
            (capped_ids, {}),
        ):
            if ids:
                await session.execute(
                    update(HomeworkJob)
                    .where(HomeworkJob.id.in_(ids))
                    .values(**base_values, **extra)
                )
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
        # Ledger the OLD (pre-rotation) token per reclaimed id.
        for job_id, old_token, _did_execute, _reclaims in stale_rows:
            await lease_events.append_event(
                session,
                job_id=job_id,
                claim_token=old_token,
                event_type=lease.EVENT_RECLAIMED_STALE,
                reason="stale claim reclaimed (owner absent from registry)",
            )
        for job_id, old_token, _did_execute, _reclaims in forced_rows:
            await lease_events.append_event(
                session,
                job_id=job_id,
                claim_token=old_token,
                event_type=lease.EVENT_RECLAIMED_FORCED,
                reason="forced reclaim past hard deadline",
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
            claim_token=None,
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
    claim_token: Optional[UUID] = None,
) -> object:
    """Record a failed attempt. Either re-schedules with exponential backoff
    (status='pending', scheduled_at in the future) or marks terminal failure
    (status='failed') if attempts exhausted.

    Returns the resulting status ('pending' = will retry, 'failed' = terminal).

    With ``claim_token=None``, the legacy result and job-transition semantics
    are preserved. When a token is given, the write is fenced: the job id on a
    retry/terminal hit,
    ``lease.LeaseLost`` when the lease is gone, or ``lease.CancelRequested`` when
    a user cancel won (the repo finalizes internally — single-finalize
    contract). The retry path also CLEARS ``claim_token`` (a job going back to
    `pending` must not carry a stale lease). After a guarded transition wins,
    unfinished phase rows owned by that lease are reconciled in the same
    caller-owned transaction: pending for a bounded retry, failed on terminal
    exhaustion. Done rows stay frozen."""
    if claim_token is not None:
        job = await session.get(HomeworkJob, job_id, populate_existing=True)
        if job is None:
            return lease.LeaseLost
        if job.attempts >= max_attempts:
            # NB: this terminal transition deliberately does NOT clear
            # `claim_token` (unlike the retry->pending branch below). That is
            # load-bearing for heartbeat_check's FINISHED path: a worker that
            # just failed its OWN job must still carry the token so the beat
            # reports FINISHED (own terminal), not LOST — clearing it here would
            # regress that case and cancel the worker mid-cleanup (D1 gate review).
            values = {
                "status": "failed",
                "completed_at": func.now(),
                "error_message": error_message,
                "last_error": error_message,
                "claimed_at": None,
                "claimed_by": None,
            }
            release_event = lease.EVENT_RELEASED_FAILED
        else:
            delay = backoff_seconds * (2 ** (job.attempts - 1))
            values = {
                "status": "pending",
                "scheduled_at": func.now()
                + func.make_interval(0, 0, 0, 0, 0, 0, delay),
                "last_error": error_message,
                "current_phase": None,
                "claimed_at": None,
                "claimed_by": None,
                "claim_token": None,  # requeue-to-pending clears the stale lease
                # `reclaims` counts CONSECUTIVE never-executed reclaims, so real
                # execution must clear it. Reaching here proves the job ran and
                # failed inside a phase. Without this reset a job could bank 20
                # never-started reclaims, then execute and fail normally, and have
                # its NEXT never-started reclaim treated as over the cap — charging
                # an execution attempt for a scheduling failure, which is the exact
                # bug the reclaims counter exists to prevent.
                "reclaims": 0,
            }
            release_event = lease.EVENT_RELEASED_RETRY
        outcome = await _fenced_update(
            session,
            job_id,
            claim_token,
            values,
            status_guard=(HomeworkJob.status == "running"),
            release_event=release_event,
        )
        if outcome == job_id:
            terminal = job.attempts >= max_attempts
            await phase_repo.reset_abandoned_phases(
                session,
                [job_id],
                status="failed" if terminal else "pending",
                error_message=(
                    error_message if terminal else None
                ),
                source_statuses=("pending", "running"),
                # The scheduler already reset cancelled siblings to pending
                # and cleared their phase tokens before this worker-level
                # terminal write.  Once the fenced parent transition wins the
                # job is failed and cannot be reclaimed, so terminal cleanup
                # must include those same-run tokenless siblings.  Retriable
                # transitions remain token-filtered to protect a new owner.
                claim_token=None if terminal else claim_token,
            )
        return outcome

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
        await phase_repo.reset_abandoned_phases(
            session,
            [job_id],
            status="failed",
            error_message=error_message,
            source_statuses=("pending", "running"),
        )
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
    await phase_repo.reset_abandoned_phases(
        session,
        [job_id],
        status="pending",
        source_statuses=("pending", "running"),
    )
    return "pending"


async def requeue_session_limited(
    session: AsyncSession,
    job_id: UUID,
    *,
    error: str,
    claim_token: Optional[UUID] = None,
) -> object:
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

    With ``claim_token=None``, the legacy result and job-transition semantics
    are preserved. When a token is given, the requeue is fenced —
    the job id on success, ``lease.LeaseLost`` / ``lease.CancelRequested`` when
    the lease no longer owns the row (the requeue already clears claim_token).
    A successful transition also resets unfinished owned phase rows to pending
    in the same caller-owned transaction; done rows remain frozen.
    """
    requeue_values = dict(
        status="pending",
        attempts=func.greatest(HomeworkJob.attempts - 1, 0),
        claimed_at=None,
        claimed_by=None,
        claim_token=None,
        current_phase=None,
        last_error=error,
        scheduled_at=func.now(),
    )
    if claim_token is not None:
        outcome = await _fenced_update(
            session,
            job_id,
            claim_token,
            requeue_values,
            status_guard=(HomeworkJob.status == "running"),
            release_event=lease.EVENT_RELEASED_RETRY,
        )
        if outcome == job_id:
            await phase_repo.reset_abandoned_phases(
                session,
                [job_id],
                status="pending",
                source_statuses=("pending", "running"),
                claim_token=claim_token,
            )
        return outcome
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
        .values(**requeue_values)
    )
    if result.rowcount == 0:
        return await _finalize_if_cancelling(session, job_id)
    await phase_repo.reset_abandoned_phases(
        session,
        [job_id],
        status="pending",
        source_statuses=("pending", "running"),
    )
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
    claim_token: Optional[UUID] = None,
) -> object:
    """Park a job whose api call exhausted the fleet credential-slot wait.

    Like requeue_session_limited: attempt refunded (claim's increment is
    compensated), claim cleared, NOT failed. Unlike it: scheduled_at is
    pushed cooldown_seconds into the future (DB clock) so the fleet backs
    off the saturated credential instead of thrashing re-claims.

    Guarded on status='running' (gate correction 6): a concurrent cancel
    must win — returns "parked", "cancelled", or "skipped".

    With ``claim_token=None``, the legacy result and job-transition semantics
    are preserved. When a token is given, the park is fenced —
    the job id on success, ``lease.LeaseLost`` / ``lease.CancelRequested`` when
    the lease no longer owns the row (the park already clears claim_token).
    A successful transition also resets unfinished owned phase rows to pending
    in the same caller-owned transaction; done rows remain frozen."""
    park_values = dict(
        status="pending",
        attempts=func.greatest(HomeworkJob.attempts - 1, 0),
        claimed_at=None,
        claimed_by=None,
        claim_token=None,
        current_phase=None,
        last_error=error,
        scheduled_at=func.now()
        + func.make_interval(0, 0, 0, 0, 0, 0, cooldown_seconds),
    )
    if claim_token is not None:
        outcome = await _fenced_update(
            session,
            job_id,
            claim_token,
            park_values,
            status_guard=(HomeworkJob.status == "running"),
            release_event=lease.EVENT_RELEASED_RETRY,
        )
        if outcome == job_id:
            await phase_repo.reset_abandoned_phases(
                session,
                [job_id],
                status="pending",
                source_statuses=("pending", "running"),
                claim_token=claim_token,
            )
        return outcome
    result = await session.execute(
        update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "running")
        .values(**park_values)
    )
    if result.rowcount > 0:
        await phase_repo.reset_abandoned_phases(
            session,
            [job_id],
            status="pending",
            source_statuses=("pending", "running"),
        )
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


async def resume_failed_in_batch(
    session: AsyncSession, batch_id: UUID, *,
    wave_size: int = 0, interval_seconds: int = 0,
) -> dict:
    """Re-enqueue every failed/cancelled job in a batch via reset_for_retry
    (status->pending, attempts->0). reset_for_retry keeps phase rows, so the
    pipeline RESUMES — done phases are reused, only unfinished ones re-run.

    SKIPS any job pinned to a retired model (gemini-2.5, retired 2026-08-03,
    see job_reactivation.retired_models_in_job) instead of resuming it — resume
    reuses the job's pinned provider/model verbatim, so re-enqueuing a
    retired-stamped job would call a dead model.

    ``wave_size``/``interval_seconds``: launch stagger (plan 2026-08-11).
    Resuming N failed lessons makes them all claimable at once, which is the
    same synchronised burst a fresh batch launch produces — and resume is the
    LIKELIER re-trigger, since retrying is how operators react to the failure.
    Defaults of 0 mean "no stagger", so any other caller is unaffected.

    Returns ``{"resumed": <count re-enqueued>, "skipped_retired": [<job id
    str>, ...]}``.
    """
    from app.services import job_reactivation
    from app.services.launch_stagger import stagger_offset

    rows = await session.execute(
        select(HomeworkJob)
        .where(
            HomeworkJob.batch_id == batch_id,
            HomeworkJob.status.in_(["failed", "cancelled"]))
        # Deterministic wave assignment: with no ORDER BY the DB may return rows
        # in any order, so which lesson lands in wave 0 would vary run to run.
        .order_by(HomeworkJob.created_at, HomeworkJob.id))
    jobs = list(rows.scalars().all())
    resumed = 0
    skipped_retired: list[str] = []
    for job in jobs:
        if job_reactivation.retired_models_in_job(job):
            skipped_retired.append(str(job.id))
            continue
        # Wave position is `resumed`, NOT the loop index: a skipped retired job
        # adds no load and must not consume a wave slot.
        await reset_for_retry(
            session, job.id,
            start_offset_seconds=stagger_offset(
                resumed, wave_size=wave_size, interval_seconds=interval_seconds))
        resumed += 1
    return {"resumed": resumed, "skipped_retired": skipped_retired}


async def latest_for_section(
    session: AsyncSession, book_id: UUID, toc_entry_id: UUID, *,
    transport: Optional[str] = None,
    output_language: str,
    kind: str = "homework",
) -> Optional[HomeworkJob]:
    """The most recent job for a (book, section, output_language) regardless of
    status — used by relaunch to find a failed/cancelled job to RESUME rather
    than recreate.

    `output_language` scopes the lookup so an EN relaunch over a previously-
    failed UZ section finds the EN job (not the UZ one) and resumes it correctly
    instead of creating a new EN job that would duplicate work.

    `kind` scopes the lookup so a 'teacher_material' relaunch never resumes a
    'homework' job (and vice versa). Default 'homework' keeps every existing
    caller behavior-identical.
    """
    conds = [HomeworkJob.book_id == book_id,
             HomeworkJob.toc_entry_id == toc_entry_id,
             HomeworkJob.output_language == output_language,
             HomeworkJob.kind == kind]
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
        .values(
            status="cancelled",
            completed_at=func.now(),
            claimed_at=None,
            claimed_by=None,
            claim_token=None,
        )
    )
    return result.rowcount or 0
