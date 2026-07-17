import asyncio
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.config import settings
from app.db import SessionLocal, get_session
from app.repositories import agent_usage as agent_usage_repo
from app.repositories import books as books_repo
from app.repositories import cost as cost_repo
from app.repositories import jobs as jobs_repo
from app.repositories import launch_defaults as launch_defaults_repo
from app.repositories import toc_entries as toc_repo
from app.repositories import workers as workers_repo
from app.schemas import GenerateRequest, JobOut, PhaseOut
from app.services import events_bus, notion_archive, pricing
from app.services.agent_models import (
    MODEL_MANIFEST,
    API_ONLY_PROVIDERS,
    api_supported,
    is_valid,
    resolve_output_language_for_book,
    resolve_role_selection,
    resolve_role_transport,
    resolve_role_transport_default,
    validate_output_language,
    validate_role_transport,
    validate_role_provider,
    validate_transport,
)
from app.services.flows import order_phase_selection, flow_for, selection_missing_prompts
from app.services.providers import PROVIDERS
from app.services.worker import RUNNING_JOBS

router = APIRouter(tags=["jobs"])

log = logging.getLogger(__name__)

# Idempotency-Key → job_id cache. Bounded; oldest entries evicted when the
# limit is hit. Each entry expires after `_IDEMPOTENCY_TTL_SECONDS`.
# In-memory is fine for single-process; multi-process deployments would
# need a shared store (Redis, Postgres table) — but at this scale, the
# advisory lock + natural-key idempotency in the DB is the load-bearing
# mechanism. The header cache is a nice-to-have for client retry safety.
import time

_IDEMPOTENCY_CACHE: dict[str, tuple[UUID, float]] = {}
_IDEMPOTENCY_TTL_SECONDS = 24 * 3600  # 24 hours
_IDEMPOTENCY_MAX_ENTRIES = 10_000


def _idempotency_get(key: str) -> Optional[UUID]:
    entry = _IDEMPOTENCY_CACHE.get(key)
    if entry is None:
        return None
    job_id, expires_at = entry
    if time.time() > expires_at:
        _IDEMPOTENCY_CACHE.pop(key, None)
        return None
    return job_id


def _idempotency_set(key: str, job_id: UUID) -> None:
    if len(_IDEMPOTENCY_CACHE) >= _IDEMPOTENCY_MAX_ENTRIES:
        # Evict the oldest 10% to make room. Cheap O(n) scan; fine at this size.
        sorted_keys = sorted(_IDEMPOTENCY_CACHE.items(), key=lambda kv: kv[1][1])
        for k, _ in sorted_keys[: _IDEMPOTENCY_MAX_ENTRIES // 10]:
            _IDEMPOTENCY_CACHE.pop(k, None)
    _IDEMPOTENCY_CACHE[key] = (job_id, time.time() + _IDEMPOTENCY_TTL_SECONDS)


def _phase_zip(phase_outputs) -> bytes:
    """Zip one `<NN>-<phase>.md` per completed, non-extract phase that has md."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(phase_outputs, key=lambda p: p.phase_order):
            if p.phase_name == "extract" or p.status != "done" or not (p.output_md or "").strip():
                continue
            zf.writestr(f"{p.phase_order:02d}-{p.phase_name}.md", p.output_md)
    return buf.getvalue()


@router.post("/books/{book_id}/sections/{toc_entry_id}/generate", status_code=201)
async def generate(
    book_id: UUID,
    toc_entry_id: UUID,
    response: Response,
    body: GenerateRequest = GenerateRequest(),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Generate (or return) a homework job for a section.

    **Idempotency** — three layers, in order of precedence:

    1. `Idempotency-Key` header: client-supplied (typically a UUID v4). If
       the same key is reused within 24h, the original job is returned
       regardless of body. Client-side retry safety for network blips.

    2. Natural-key idempotency: when `force=False` (default), an existing
       pending / running / done job for this (book, section) is returned
       instead of creating a duplicate. Subsequent same-section calls reuse.

    3. Postgres advisory lock on (book, section): serializes concurrent
       requests so a double-click can't race past the natural-key check
       and create two jobs simultaneously.

    `force=True` skips layer 2 (creates a fresh job) but still respects
    layers 1 and 3.
    """
    # Layer 1: header-key idempotency (fast path, no DB hit if cached).
    if idempotency_key:
        cached_job_id = _idempotency_get(idempotency_key)
        if cached_job_id is not None:
            response.status_code = 200
            try:
                return await _job_out(session, cached_job_id)
            except HTTPException:
                # Cached job was deleted upstream — invalidate and fall through.
                _IDEMPOTENCY_CACHE.pop(idempotency_key, None)

    # Book-scoped SHARED advisory lock (BE-02 task 3) — taken BEFORE the book
    # fetch below, so that fetch doubles as the post-lock re-read: a
    # concurrent DELETE holding the EXCLUSIVE form blocks us here until it
    # commits/rolls back, and our subsequent read always sees current state
    # (never a stale pre-lock book object).
    await books_repo.lock_book_shared(session, book_id)
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")
    section = await toc_repo.get(session, toc_entry_id)
    if section is None or section.book_id != book_id:
        raise HTTPException(404, "section not found")

    if not is_valid(body.provider, body.model):
        raise HTTPException(
            400,
            f"unknown (provider, model) pair: ({body.provider!r}, {body.model!r}). "
            f"Allowed providers: {sorted(MODEL_MANIFEST)}.",
        )

    transport_err = validate_transport(body.provider, body.model, body.transport)
    if transport_err is not None:
        raise HTTPException(400, transport_err)

    for field, value in (
        ("extract_transport", body.extract_transport),
        ("judge_transport", body.judge_transport),
        ("solver_transport", body.solver_transport),
    ):
        role_err = validate_role_transport(field, value)
        if role_err is not None:
            raise HTTPException(400, role_err)

    # ── custom prompts + phase subset validation (Gate 2/3, fail before DB) ──
    custom_prompts = body.custom_prompts or None
    if custom_prompts:
        valid_phases = set(flow_for(book.subject))
        for phase, md in custom_prompts.items():
            if phase == "extract" or phase not in valid_phases:
                raise HTTPException(400, f"custom_prompts: unknown phase {phase!r}")
            if len(md) > 20_000:
                raise HTTPException(
                    400, f"custom_prompts[{phase}] too long ({len(md)} chars; max 20000).")

    selected_phases: Optional[list[str]] = None
    if body.selected_phases is not None:
        try:
            selected_phases = order_phase_selection(book.subject, body.selected_phases)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # Pick-phases requires an uploaded prompt for every picked phase.
        missing = selection_missing_prompts(selected_phases, custom_prompts)
        if missing:
            raise HTTPException(
                400, f"selected phases missing a custom prompt: {missing}")

    if (err := validate_output_language(body.output_language, allow_none=True)):
        raise HTTPException(400, err)

    # Gate 1: a custom/subset launch must never reuse a plain job.
    force_fresh = body.force or bool(custom_prompts) or selected_phases is not None
    # Per-role provider/model: validate only explicit picks. The role's effective
    # transport decides whether an explicit model is mandatory.
    for role, prov, mdl, role_tx in (
        ("extract", body.extract_provider, body.extract_model, body.extract_transport),
        ("judge", body.judge_provider, body.judge_model, body.judge_transport),
        ("solver", body.solver_provider, body.solver_model, body.solver_transport),
    ):
        if prov is None:
            continue
        if not is_valid(prov, mdl):
            raise HTTPException(400, f"{role}: unknown (provider, model) ({prov!r}, {mdl!r})")
        if role_err := validate_role_provider(role, prov):
            raise HTTPException(400, role_err)
        eff_tx = resolve_role_transport(role_tx, body.transport)
        err = validate_transport(prov, mdl, eff_tx)
        if err is not None:
            raise HTTPException(400, f"{role}: {err}")

    # Layer 3: serialize concurrent requests for the same (book, section).
    # Lock is held for the rest of this transaction and auto-released on
    # commit, so the second concurrent request waits and then sees the
    # job the first one just created.
    await jobs_repo.lock_section_for_generate(session, book_id, toc_entry_id)

    # Fetch the launch_defaults singleton early so res_output_language is
    # available for the idempotency lookup below (language-scoped dedup).
    ld = await launch_defaults_repo.get(session)
    res_output_language = resolve_output_language_for_book(
        body.output_language, book.source_language, ld.output_language)

    # Layer 2: natural-key idempotency.
    if not force_fresh:
        existing = await jobs_repo.find_active_for_section(
            session, book_id, toc_entry_id, transport=body.transport,
            output_language=res_output_language
        )
        if existing is not None:
            await session.commit()  # release the advisory lock
            if idempotency_key:
                _idempotency_set(idempotency_key, existing.id)
            response.status_code = 200
            return await _job_out(session, existing.id)

    # fleet-api-4: never-pay-twice — check for prior api spend before creating
    # a force-regenerated job so the operator sees what they're about to re-bill.
    # Runs BEFORE the job create so the lookup is inside the advisory-lock window.
    prior_api_cost_usd: float | None = None
    would_rebill: bool | None = None
    if body.force:
        _prior_cost, _had_done_api_job = await cost_repo.section_prior_api_cost(
            session, book_id, toc_entry_id, body.transport)
        prior_api_cost_usd = _prior_cost
        would_rebill = _had_done_api_job and _prior_cost > 0

    # Backpressure: if the eligible-now queue is too deep, refuse to enqueue
    # rather than letting it grow unbounded. The client can retry later.
    # Skipped when limit=0 (disabled).
    if settings.queue_backpressure_limit > 0:
        depth = await jobs_repo.queue_depth(session)
        if depth >= settings.queue_backpressure_limit:
            await session.commit()
            raise HTTPException(
                status_code=503,
                detail=(
                    f"queue is full ({depth} jobs waiting); please retry shortly"
                ),
                headers={"Retry-After": "30"},
            )

    # Resolve roles against the UI-managed global defaults: explicit pick wins,
    # else the global default. Stamp CONCRETE provider/model onto the job so it
    # is self-describing (future-launches-only; agent_usages attribution stays honest).
    res_judge_provider, res_judge_model = resolve_role_selection(
        body.judge_provider, body.judge_model, ld.judge_provider, ld.judge_model)
    res_extract_provider, res_extract_model = resolve_role_selection(
        body.extract_provider, body.extract_model, ld.extract_provider, ld.extract_model)
    res_solver_provider, res_solver_model = resolve_role_selection(
        body.solver_provider, body.solver_model, ld.solver_provider, ld.solver_model)
    res_judge_transport = resolve_role_transport_default(body.judge_transport, ld.judge_transport)
    res_extract_transport = resolve_role_transport_default(body.extract_transport, ld.extract_transport)
    res_solver_transport = resolve_role_transport_default(body.solver_transport, ld.solver_transport)
    # Defense-in-depth: resolved pairs must be manifest-valid.
    for role, prov, mdl in (("judge", res_judge_provider, res_judge_model),
                            ("extract", res_extract_provider, res_extract_model),
                            ("solver", res_solver_provider, res_solver_model)):
        if not is_valid(prov, mdl):
            raise HTTPException(500, f"{role}: resolved default off-manifest ({prov!r},{mdl!r})")
        if role_err := validate_role_provider(role, prov):
            raise HTTPException(400, f"{role} global default: {role_err}")
    # Gate: if the global default resolved a non-api-capable role provider to an
    # api effective transport, fail loud at launch rather than silently strand the
    # job unclaimable. cli-resolving transports always return None from
    # validate_transport and are never affected.
    for role, prov, mdl, res_tx in (
        ("judge", res_judge_provider, res_judge_model, res_judge_transport),
        ("extract", res_extract_provider, res_extract_model, res_extract_transport),
        ("solver", res_solver_provider, res_solver_model, res_solver_transport),
    ):
        eff_tx = resolve_role_transport(res_tx, body.transport)
        err = validate_transport(prov, mdl, eff_tx)
        if err is not None:
            raise HTTPException(400, f"{role} global default: {err}")
    job = await jobs_repo.create(
        session,
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=book.subject,
        status="pending",
        provider=body.provider,
        model=body.model,
        transport=body.transport,
        extract_transport=res_extract_transport,
        judge_transport=res_judge_transport,
        solver_transport=res_solver_transport,
        custom_prompts=custom_prompts,
        selected_phases=selected_phases,
        extract_provider=res_extract_provider,
        extract_model=res_extract_model,
        judge_provider=res_judge_provider,
        judge_model=res_judge_model,
        solver_provider=res_solver_provider,
        solver_model=res_solver_model,
        output_language=res_output_language,
    )
    await session.commit()  # commit + release advisory lock atomically

    if idempotency_key:
        _idempotency_set(idempotency_key, job.id)

    # Note: no `asyncio.create_task(pipeline.run(...))` here. The worker
    # process polls `homework_jobs.status='pending'` and claims this row.
    # See `app/services/worker.py`.
    out = await _job_out(session, job.id)
    # Attach never-pay-twice fields when force=True (additive; absent otherwise). [C4]
    if prior_api_cost_usd is not None:
        out.prior_api_cost_usd = prior_api_cost_usd
        out.would_rebill = would_rebill
    # We run exactly the picked phases now (no dependency auto-expansion), so
    # nothing is ever auto-added. Field kept for response-shape stability. [PR37]
    out.added_phases = []
    response.status_code = 201
    return out


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    return await _job_out(session, job_id)


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Retry a failed job in place — reuses the same job row (and pinned
    provider/model) instead of creating a fresh one.

    Distinct from the `force=True` path on `/generate`, which is the
    "regenerate from scratch" affordance. This endpoint resets the job back
    to `pending` and zeroes the queue retry counter so the worker re-claims
    it as a fresh attempt. The pipeline is idempotent against existing phase
    rows, so no cleanup is needed.

    Retry *resumes*: it reuses completed phase rows (worklog 0031 resume) and
    re-runs only the rest. Use `force=true` on `/generate` for a clean
    from-scratch redo.

    Accepts `failed` or `cancelled`; refuses anything else with 409 — there's
    no point retrying a pending/running/done job.
    """
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    book_id = job.book_id
    # Book-scoped SHARED advisory lock (BE-02 task 3) — retry doesn't know the
    # book_id up front, so it derives it from the just-fetched job FIRST, then
    # locks, then RE-FETCHES the job: a concurrent DELETE holding the
    # EXCLUSIVE form blocks us here, and the re-fetch below sees whatever
    # state remains once it releases (the job may have vanished while we
    # waited — never resurrect it).
    await books_repo.lock_book_shared(session, book_id)
    # `Session.get()` short-circuits via the identity map: without expiring
    # the pre-lock `job` object first, the "re-fetch" below would silently
    # hand back that SAME stale in-memory object (even if the row was deleted
    # while we waited for the lock) instead of re-querying — caught for real
    # by tests/integration/test_book_delete_race.py (StaleDataError on the
    # eventual UPDATE, not a clean 404).
    session.expire(job)
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(
            409,
            f"only failed or cancelled jobs can be retried; current status={job.status!r}",
        )
    updated = await jobs_repo.reset_for_retry(session, job_id)
    if updated is None:
        # Race: row was deleted between the get() and the reset. Treat as 404.
        raise HTTPException(404, "job not found")
    await session.commit()
    return await _job_out(session, job_id)


# In-flight force re-archives, keyed by job id — mirrors the batch sweep's
# `_REARCHIVE_TASKS` double-click guard (batch.py). A full-packet force push is
# >5 min of rate-limited Notion I/O; run INLINE it gets CANCELLED by a client
# disconnect mid-clear, leaving a half-written page (hit live during the #81
# verify). Single-process guard (head runs the archive), same caveats as batch.
_FORCE_REARCHIVE_TASKS: dict[UUID, "asyncio.Task"] = {}


async def _force_rearchive_one(job_id: UUID) -> None:
    """Run the best-effort force re-archive in the background. archive_job
    already swallows + records skip reasons; this wrapper is defensive and
    releases the in-flight guard."""
    try:
        await notion_archive.archive_job(job_id, force=True)
    except Exception:  # defensive; archive_job is already best-effort
        log.warning("force re-archive of job %s failed (non-fatal)",
                    job_id, exc_info=True)
    finally:
        _FORCE_REARCHIVE_TASKS.pop(job_id, None)


@router.post("/jobs/{job_id}/retry-archive")
async def retry_archive_job(
    job_id: UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Re-attempt the best-effort Notion archive for a job. Normally for a job
    whose push previously failed (status=done, notion_archived_at IS NULL);
    `archive_job` is idempotent (skips already-populated pages) and clears
    `notion_skip_reason` on success — runs inline and returns the refreshed
    JobOut. With `force=true` an already-archived job is re-pushed and its leaf
    pages are cleared and rewritten (replace mode) — that push is **backgrounded**
    (the batch-sweep shape: immediate `{queued, already_running}` receipt) because
    a full packet is >5 min of Notion I/O and an inline run gets cancelled by
    client disconnects mid-clear, leaving a half-written page. A second force
    POST while one is in flight no-ops. Refuses non-done jobs with 409."""
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(
            409, f"only done jobs can be re-archived; current status={job.status!r}")
    if job.notion_archived_at is not None and not force:
        raise HTTPException(409, "job already archived to Notion")

    if force:
        if job_id in _FORCE_REARCHIVE_TASKS:
            return {"job_id": str(job_id), "queued": 0, "already_running": True}
        _FORCE_REARCHIVE_TASKS[job_id] = asyncio.create_task(
            _force_rearchive_one(job_id))
        return {"job_id": str(job_id), "queued": 1, "already_running": False}

    await notion_archive.archive_job(job_id)
    # archive_job commits in its OWN session; drop this session's stale copy so
    # _job_out re-reads the updated notion_skip_reason/notion_archived_at.
    session.expire_all()
    return await _job_out(session, job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    """Cancel a job. A queued job is cancelled atomically (never starts). A
    running job is flagged `cancelling` and its in-process task (if this process
    owns it) is cancelled immediately; otherwise the owning worker self-cancels
    on its next heartbeat. Terminal jobs (done/failed/cancelled) -> 409."""
    if await jobs_repo.cancel_if_pending(session, job_id):
        await session.commit()
        job = await jobs_repo.get(session, job_id)
        return JobOut.model_validate(job)

    if await jobs_repo.request_cancel(session, job_id):
        await session.commit()
        task = RUNNING_JOBS.get(job_id)
        if task is not None:
            task.cancel()  # same-process: instant
        job = await jobs_repo.get(session, job_id)
        return JobOut.model_validate(job)

    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    raise HTTPException(409, f"cannot cancel a job with status={job.status!r}")


async def _refetch_job_event(job_id: UUID, event: str, marker: dict) -> dict:
    """Rebuild an oversized bus event from the DB (NOTIFY caps at ~8KB, so
    e.g. phase_completed's output_md travels as a __refetch__ marker). Safe
    because the pipeline persists rows before publishing. Falls back to the
    inline hint fields if the row isn't found."""
    hint = {k: v for k, v in marker.items() if k != "__refetch__"}
    async with SessionLocal() as session:
        if event == "phase_completed":
            job = await jobs_repo.get_with_phases(session, job_id)
            for p in (job.phase_outputs if job else []):
                if p.phase_order == hint.get("phase_order"):
                    return {
                        "phase_name": p.phase_name,
                        "phase_order": p.phase_order,
                        "output_md": p.output_md or "",
                        "tokens_input": p.tokens_input,
                        "tokens_output": p.tokens_output,
                    }
        elif event == "error":
            job = await jobs_repo.get(session, job_id)
            if job is not None and job.error_message:
                return {**hint, "message": job.error_message}
    return hint


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: UUID, request: Request):
    resource_id = f"job:{job_id}"

    async def event_gen():
        async with SessionLocal() as session:
            job = await jobs_repo.get_with_phases(session, job_id)
            # Snapshot everything we need into plain dicts INSIDE the session
            # block, then release the connection BEFORE yielding. Yielding while
            # the session is still checked out can orphan the pooled connection
            # if the client disconnects mid-yield (the async generator is GC'd
            # without a clean aclose()), which SQLAlchemy later reaps with a
            # "non-checked-in connection" warning.
            missing = job is None
            initial: list[dict] = []
            terminal: dict | None = None
            if not missing:
                for p in job.phase_outputs:
                    if p.status == "done":
                        initial.append({
                            "event": "phase_completed",
                            "data": json.dumps({
                                "phase_name": p.phase_name,
                                "phase_order": p.phase_order,
                                "output_md": p.output_md or "",
                                "tokens_input": p.tokens_input,
                                "tokens_output": p.tokens_output,
                            }),
                        })
                    elif p.status == "running":
                        initial.append({
                            "event": "phase_started",
                            "data": json.dumps({
                                "phase_name": p.phase_name,
                                "phase_order": p.phase_order,
                            }),
                        })
                if job.status == "done":
                    terminal = {
                        "event": "job_completed",
                        "data": json.dumps({
                            "job_id": str(job_id),
                            "download_url": f"/api/v1/jobs/{job_id}/download",
                        }),
                    }
                elif job.status == "failed":
                    terminal = {
                        "event": "error",
                        "data": json.dumps({"message": job.error_message or "failed"}),
                    }
                elif job.status in ("cancelling", "cancelled"):
                    # Cancellation publishes NO terminal event to the live bus:
                    # ``pipeline.run``'s ``finally: events_bus.close()`` tears the
                    # bus down before the worker commits ``cancelled``. So a fresh
                    # stream for an already-cancelled/cancelling job must return
                    # terminally from the initial replay instead of subscribing to
                    # a dead bus and blocking on ``q.get()`` forever.
                    terminal = {
                        "event": "job_cancelled",
                        "data": json.dumps({"job_id": str(job_id)}),
                    }

        # Session released — safe to yield without holding a pooled connection.
        if missing:
            yield {"event": "error", "data": json.dumps({"message": "job not found"})}
            return
        for ev in initial:
            yield ev
        if terminal is not None:
            yield terminal
            return

        q = events_bus.subscribe(resource_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                payload = await q.get()
                if payload is None:
                    break
                data = payload["data"]
                if isinstance(data, dict) and data.get("__refetch__"):
                    data = await _refetch_job_event(job_id, payload["event"], data)
                yield {"event": payload["event"], "data": json.dumps(data)}
                if payload["event"] in ("job_completed", "error"):
                    break
        finally:
            events_bus.unsubscribe(resource_id, q)

    return EventSourceResponse(event_gen())


@router.get("/jobs/{job_id}/download")
async def download(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Download the homework as a zip of one markdown file per phase."""
    job = await jobs_repo.get_with_phases(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done":
        raise HTTPException(404, "homework not ready")
    data = _phase_zip(job.phase_outputs)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="homework-{job_id}.zip"'},
    )


async def _job_out(session: AsyncSession, job_id: UUID) -> JobOut:
    job = await jobs_repo.get_with_phases(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    out = JobOut.model_validate(job)
    out.phases = [PhaseOut.model_validate(p) for p in job.phase_outputs]
    # The full content-phase list this job runs, so the UI can show every phase
    # (incl. not-yet-started ones as "queued") instead of only those that have
    # already begun. Subset jobs stored their closure; full-packet jobs run the
    # subject's whole flow. Either way `extract` is the head, excluded here.
    try:
        out.planned_phases = list(job.selected_phases) if job.selected_phases else list(flow_for(job.subject))
    except KeyError:
        out.planned_phases = list(job.selected_phases or [])
    return out


@router.get("/agent/models")
async def list_agent_models(session: AsyncSession = Depends(get_session)):
    from app.services.model_tiers import tier_of

    tiers = {
        prov: {m: tier_of(prov, m) for m in models}
        for prov, models in MODEL_MANIFEST.items()
    }
    return {
        "providers": MODEL_MANIFEST,
        "api_supported": {p: api_supported(p) for p in MODEL_MANIFEST},
        "api_only": {p: p in API_ONLY_PROVIDERS for p in MODEL_MANIFEST},
        "tiers": tiers,
        "fleet": await workers_repo.aggregate_fleet_capability(
            session, stale_after_seconds=settings.worker_registry_stale_seconds
        ),
    }


# ─── Usage dashboard ──────────────────────────────────────────────────────
# Per-provider rolling stats over fixed windows. Surfaces local consumption
# (calls + duration + tokens) issued by THIS app — the five CLIs (claude,
# kimi, codex, gemini, opencode) don't expose real quota APIs in headless mode, so
# we track what we've driven through them and compare against user-set
# caps in `settings.agent_limit_*` to estimate headroom.
_STATS_WINDOWS: list[tuple[str, timedelta]] = [
    ("1h", timedelta(hours=1)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
]
_STATS_PROVIDERS = tuple(PROVIDERS.keys())


def _limit_for(provider: str, window: str) -> int:
    """Look up `agent_limit_<provider>_<window>` on the settings object.
    Returns 0 (unmetered) for unknown combos so we degrade gracefully."""
    return int(getattr(settings, f"agent_limit_{provider}_{window}", 0))


@router.get("/agent/stats")
async def get_agent_stats(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Per-provider rolling consumption stats over 1h / 24h / 7d windows.

    For each provider we aggregate `agent_usages` rows whose `started_at`
    falls within the window, then divide by the configured cap to get
    `pct_of_limit`. When the cap is 0 (unmetered) `pct_of_limit` is null
    and the frontend renders a dash.
    """
    now = datetime.now(timezone.utc)

    # Each window is one independent SQL aggregate. Three queries total.
    providers: dict[str, dict[str, dict]] = {p: {} for p in _STATS_PROVIDERS}
    series: dict[str, dict] = {}
    for window_label, delta in _STATS_WINDOWS:
        since = now - delta
        series[window_label] = await agent_usage_repo.series_by_window(
            session, since=since, now=now
        )
        rows = await agent_usage_repo.stats_by_provider(session, since=since)
        by_provider = {row["provider"]: row for row in rows}
        model_rows = await agent_usage_repo.stats_by_provider_model(session, since=since)

        # Per-(provider, transport) $ rollup. Each row is priced via the static
        # map (cli rows resolve to $0 — no pay-per-token); we fold per-model
        # rows into one entry per (provider, auth_mode).
        transport_rows = await agent_usage_repo.stats_by_provider_transport(
            session, since=since
        )
        transports_by_provider: dict[str, dict[str, dict]] = {}
        for tr in transport_rows:
            # cli is the local CLI subprocess — no pay-per-token, so it costs us
            # $0. Only api (pay-per-token SDK) rows are priced.
            cost = (
                pricing.cost_usd(tr["provider"], tr["model_name"], tr)
                if tr["auth_mode"] == "api"
                else 0.0
            )
            slot = transports_by_provider.setdefault(tr["provider"], {})
            entry = slot.setdefault(
                tr["auth_mode"],
                {"auth_mode": tr["auth_mode"], "calls": 0, "cost_usd": 0.0},
            )
            entry["calls"] += int(tr["calls"])
            entry["cost_usd"] = round(entry["cost_usd"] + cost, 4)
        models_by_provider: dict[str, list[dict]] = {}
        for mr in model_rows:
            m_calls = int(mr["calls"])
            models_by_provider.setdefault(mr["provider"], []).append({
                "model_name": mr["model_name"] or "(default)",
                "calls": m_calls,
                "duration_secs": round(float(mr["duration_secs"]), 1),
                "prompt_tokens": int(mr["prompt_tokens"]),
                "output_tokens": int(mr["output_tokens"]),
                "cached_tokens": int(mr["cached_tokens"]),
                "success_pct": (
                    round(100.0 * int(mr["success_count"]) / m_calls, 1)
                    if m_calls > 0 else 0.0
                ),
            })
        for provider in _STATS_PROVIDERS:
            row = by_provider.get(provider)
            calls = int(row["calls"]) if row else 0
            success_count = int(row["success_count"]) if row else 0
            duration_secs = float(row["duration_secs"]) if row else 0.0
            prompt_tokens = int(row["prompt_tokens"]) if row else 0
            output_tokens = int(row["output_tokens"]) if row else 0
            cached_tokens = int(row["cached_tokens"]) if row else 0

            success_pct = (
                round(100.0 * success_count / calls, 1) if calls > 0 else 0.0
            )

            limit = _limit_for(provider, window_label)
            if limit > 0:
                pct_of_limit: Optional[float] = round(100.0 * calls / limit, 1)
                limit_value: Optional[int] = limit
            else:
                pct_of_limit = None
                limit_value = None

            providers[provider][window_label] = {
                "calls": calls,
                "duration_secs": round(duration_secs, 1),
                "prompt_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "success_pct": success_pct,
                "limit_calls_per_window": limit_value,
                "pct_of_limit": pct_of_limit,
                "models": models_by_provider.get(provider, []),
                "transports": list(transports_by_provider.get(provider, {}).values()),
            }

    return {
        "windows": [w for w, _ in _STATS_WINDOWS],
        "providers": providers,
        # Per-window time-series (12 buckets) for the summary sparklines.
        "series": series,
        # Strip microseconds and tag UTC so the response reads naturally
        # ('2026-05-06T03:14:22Z') and matches the docstring example.
        "now": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
