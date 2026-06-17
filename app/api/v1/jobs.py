import io
import json
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
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import GenerateRequest, JobOut, PhaseOut
from app.services import events_bus, pricing
from app.services.agent_models import (
    MODEL_MANIFEST,
    api_supported,
    is_valid,
    validate_role_transport,
    validate_transport,
)
from app.services.providers import PROVIDERS
from app.services.worker import RUNNING_JOBS

router = APIRouter(tags=["jobs"])

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
    ):
        role_err = validate_role_transport(field, value)
        if role_err is not None:
            raise HTTPException(400, role_err)

    # Layer 3: serialize concurrent requests for the same (book, section).
    # Lock is held for the rest of this transaction and auto-released on
    # commit, so the second concurrent request waits and then sees the
    # job the first one just created.
    await jobs_repo.lock_section_for_generate(session, book_id, toc_entry_id)

    # Layer 2: natural-key idempotency.
    if not body.force:
        existing = await jobs_repo.find_active_for_section(
            session, book_id, toc_entry_id, transport=body.transport
        )
        if existing is not None:
            await session.commit()  # release the advisory lock
            if idempotency_key:
                _idempotency_set(idempotency_key, existing.id)
            response.status_code = 200
            return await _job_out(session, existing.id)

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

    job = await jobs_repo.create(
        session,
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=book.subject,
        status="pending",
        provider=body.provider,
        model=body.model,
        transport=body.transport,
        extract_transport=body.extract_transport,
        judge_transport=body.judge_transport,
    )
    await session.commit()  # commit + release advisory lock atomically

    if idempotency_key:
        _idempotency_set(idempotency_key, job.id)

    # Note: no `asyncio.create_task(pipeline.run(...))` here. The worker
    # process polls `homework_jobs.status='pending'` and claims this row.
    # See `app/services/worker.py`.
    return await _job_out(session, job.id)


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
                yield {"event": payload["event"], "data": json.dumps(payload["data"])}
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
    return out


@router.get("/agent/models")
async def list_agent_models():
    return {
        "providers": MODEL_MANIFEST,
        "api_supported": {p: api_supported(p) for p in MODEL_MANIFEST},
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
