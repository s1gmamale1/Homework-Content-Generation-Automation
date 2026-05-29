import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
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
from app.services import events_bus
from app.services.agent_models import MODEL_MANIFEST, is_valid

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

    # Layer 3: serialize concurrent requests for the same (book, section).
    # Lock is held for the rest of this transaction and auto-released on
    # commit, so the second concurrent request waits and then sees the
    # job the first one just created.
    await jobs_repo.lock_section_for_generate(session, book_id, toc_entry_id)

    # Layer 2: natural-key idempotency.
    if not body.force:
        existing = await jobs_repo.find_active_for_section(session, book_id, toc_entry_id)
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

    Refuses anything other than `failed` with 409 — there's no point retrying
    a pending/running/done job.
    """
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "failed":
        raise HTTPException(
            409,
            f"only failed jobs can be retried; current status={job.status!r}",
        )
    updated = await jobs_repo.reset_for_retry(session, job_id)
    if updated is None:
        # Race: row was deleted between the get() and the reset. Treat as 404.
        raise HTTPException(404, "job not found")
    await session.commit()
    return await _job_out(session, job_id)


@router.get("/jobs/{job_id}/stream")
async def stream_job(job_id: UUID, request: Request):
    resource_id = f"job:{job_id}"

    async def event_gen():
        async with SessionLocal() as session:
            job = await jobs_repo.get_with_phases(session, job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"message": "job not found"})}
                return

            for p in job.phase_outputs:
                if p.status == "done":
                    yield {
                        "event": "phase_completed",
                        "data": json.dumps({
                            "phase_name": p.phase_name,
                            "phase_order": p.phase_order,
                            "output_md": p.output_md or "",
                            "tokens_input": p.tokens_input,
                            "tokens_output": p.tokens_output,
                        }),
                    }
                elif p.status == "running":
                    yield {
                        "event": "phase_started",
                        "data": json.dumps({
                            "phase_name": p.phase_name,
                            "phase_order": p.phase_order,
                        }),
                    }

            if job.difficulty is not None:
                yield {
                    "event": "difficulty_classified",
                    "data": json.dumps({"difficulty": job.difficulty}),
                }

            if job.status == "done":
                yield {
                    "event": "job_completed",
                    "data": json.dumps({
                        "job_id": str(job_id),
                        "download_url": f"/api/v1/jobs/{job_id}/download",
                    }),
                }
                return
            if job.status == "failed":
                yield {
                    "event": "error",
                    "data": json.dumps({"message": job.error_message or "failed"}),
                }
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
    format: str = "zip",
    session: AsyncSession = Depends(get_session),
):
    """Download the assembled homework. Default format is `zip` (homework.md
    + games.json packaged together). Pass `?format=md` for the bare markdown."""
    job = await jobs_repo.get(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "done" or job.assembled_md is None:
        raise HTTPException(404, "homework not ready")

    if format == "md":
        return PlainTextResponse(
            job.assembled_md,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="homework-{job_id}.md"'
            },
        )

    # Default: zip bundle (homework.md + structured JSONs for the interactive phases)
    structured_files: dict[str, dict] = {
        "games.json": job.games_json or {"games": []},
        "flashcards.json": job.flashcards_json or {"cards": []},
        "final-challenge.json": job.final_challenge_json or {"questions": []},
        "memory-sprint.json": job.memory_sprint_json or {"items": []},
        "reading.json": job.reading_json or {"passage_md": "", "checkpoints": []},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("homework.md", job.assembled_md)
        for filename, payload in structured_files.items():
            zf.writestr(filename, json.dumps(payload, ensure_ascii=False, indent=2))
    buf.seek(0)
    return StreamingResponse(
        buf,
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
    return {"providers": MODEL_MANIFEST}


# ─── Usage dashboard ──────────────────────────────────────────────────────
# Per-provider rolling stats over fixed windows. Surfaces local consumption
# (calls + duration + tokens) issued by THIS app — the four CLIs (claude,
# kimi, codex, gemini) don't expose real quota APIs in headless mode, so
# we track what we've driven through them and compare against user-set
# caps in `settings.agent_limit_*` to estimate headroom.
_STATS_WINDOWS: list[tuple[str, timedelta]] = [
    ("1h", timedelta(hours=1)),
    ("24h", timedelta(hours=24)),
    ("7d", timedelta(days=7)),
]
_STATS_PROVIDERS = ("claude", "kimi", "codex", "gemini")


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
    for window_label, delta in _STATS_WINDOWS:
        since = now - delta
        rows = await agent_usage_repo.stats_by_provider(session, since=since)
        by_provider = {row["provider"]: row for row in rows}
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
            }

    return {
        "windows": [w for w, _ in _STATS_WINDOWS],
        "providers": providers,
        # Strip microseconds and tag UTC so the response reads naturally
        # ('2026-05-06T03:14:22Z') and matches the docstring example.
        "now": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
