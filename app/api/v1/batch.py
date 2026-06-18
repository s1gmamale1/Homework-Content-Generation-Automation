from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.services import subjects
from app.services.agent_models import (
    is_valid,
    resolve_role_transport,
    validate_role_transport,
    validate_transport,
)

router = APIRouter(tags=["batches"])

# Fleet batches default to the cli-first provider (master spec §1a); diverges
# from /generate's gemini default deliberately. model=None -> provider default.
_DEFAULT_PROVIDER = "claude"


class BatchLaunchRequest(BaseModel):
    book_id: UUID
    toc_entry_ids: Optional[list[UUID]] = None  # None = all lessons
    provider: Optional[str] = None
    model: Optional[str] = None
    transport: str = "cli"
    extract_transport: str = "inherit"   # per-role override; "inherit" follows `transport`
    judge_transport: str = "inherit"
    extract_provider: Optional[str] = None
    extract_model: Optional[str] = None
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    force: bool = False
    preview: bool = False                 # compute disposition, don't mutate
    relaunch_mode: str = "resume"         # "resume" | "discard" for failed/cancelled-with-saved


def _rollup_payload(batch, tally: dict[str, int], original_filename: str | None = None) -> dict:
    return {
        "batch_id": str(batch.id),
        "book_id": str(batch.book_id),
        "subject": batch.subject,
        "subject_variant": subjects.history_variant(batch.subject, original_filename),
        "grade": batch.grade,
        "provider": batch.provider,
        "model": batch.model,
        "transport": batch.transport,
        "extract_transport": batch.extract_transport,
        "judge_transport": batch.judge_transport,
        "extract_provider": batch.extract_provider,
        "extract_model": batch.extract_model,
        "judge_provider": batch.judge_provider,
        "judge_model": batch.judge_model,
        "rollup": tally,
        "lessons_covered": sum(tally.values()),
        "complete": (tally.get("pending", 0) + tally.get("running", 0)
                     + tally.get("cancelling", 0)) == 0 and sum(tally.values()) > 0,
        "created_at": batch.created_at.isoformat(),
    }


@router.post("/jobs/batch", status_code=201)
async def launch_batch(
    body: BatchLaunchRequest,
    session: AsyncSession = Depends(get_session),
):
    book = await books_repo.get(session, body.book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status in ("uploading", "toc_extracting"):
        raise HTTPException(409, "book still extracting — lessons available once TOC extraction completes")
    if book.status == "failed":
        raise HTTPException(409, f"book extraction failed: {book.error_message or 'unknown error'}")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")

    lessons = await toc_repo.list_for_book(session, body.book_id)
    if not lessons:
        raise HTTPException(422, "no lessons found for this book")

    by_id = {t.id: t for t in lessons}
    if body.toc_entry_ids is not None:
        bad = [tid for tid in body.toc_entry_ids if tid not in by_id]
        if bad:
            raise HTTPException(422, f"toc_entry_ids not in this book: {bad}")
        targets = [by_id[tid] for tid in body.toc_entry_ids]
    else:
        targets = lessons

    provider = body.provider or _DEFAULT_PROVIDER
    if not is_valid(provider, body.model):
        raise HTTPException(400, f"invalid provider/model: {provider}/{body.model}")

    transport_err = validate_transport(provider, body.model, body.transport)
    if transport_err is not None:
        raise HTTPException(400, transport_err)

    for field, value in (
        ("extract_transport", body.extract_transport),
        ("judge_transport", body.judge_transport),
    ):
        role_err = validate_role_transport(field, value)
        if role_err is not None:
            raise HTTPException(400, role_err)

    # Per-role provider/model: validate only explicit picks. The role's effective
    # transport decides whether an explicit model is mandatory.
    for role, prov, mdl, role_tx in (
        ("extract", body.extract_provider, body.extract_model, body.extract_transport),
        ("judge", body.judge_provider, body.judge_model, body.judge_transport),
    ):
        if prov is None:
            continue
        if not is_valid(prov, mdl):
            raise HTTPException(400, f"{role}: unknown (provider, model) ({prov!r}, {mdl!r})")
        eff_tx = resolve_role_transport(role_tx, body.transport)
        err = validate_transport(prov, mdl, eff_tx)
        if err is not None:
            raise HTTPException(400, f"{role}: {err}")

    # (a) STRICT zero-write preview — compute disposition and return BEFORE any
    # batch create/mutation. Leaves no phantom batch row in rollups.
    if body.preview:
        new = resumable = empty = 0
        for t in targets:
            active = await jobs_repo.find_active_for_section(
                session, body.book_id, t.id, transport=body.transport)
            if active is not None:
                continue  # pending/running/done — not "remaining"
            latest = await jobs_repo.latest_for_section(
                session, body.book_id, t.id, transport=body.transport)
            if latest is not None and latest.status in ("failed", "cancelled"):
                if await jobs_repo.done_phase_count_for_job(session, latest.id) > 0:
                    resumable += 1
                else:
                    empty += 1
            else:
                new += 1
        return JSONResponse(
            status_code=200,
            content={"book_id": str(body.book_id), "preview": True,
                     "new": new, "resumable": resumable, "empty": empty})

    batch = await batches_repo.get_or_create_for_book(
        session, book_id=body.book_id, subject=book.subject, grade=book.grade,
        provider=provider, model=body.model, transport=body.transport,
        extract_transport=body.extract_transport,
        judge_transport=body.judge_transport,
        extract_provider=body.extract_provider,
        extract_model=body.extract_model,
        judge_provider=body.judge_provider,
        judge_model=body.judge_model)

    created = adopted = skipped = resumed = 0
    for t in targets:
        await jobs_repo.lock_section_for_generate(session, body.book_id, t.id)
        # Transport-scoped lookup (spec §9a): an api batch over a cli-generated
        # book finds no same-transport job → falls through to create, leaving
        # the cli jobs untouched.
        existing = None if body.force else await jobs_repo.find_active_for_section(
            session, body.book_id, t.id, transport=body.transport)
        if existing is not None:
            # Lookup is transport-scoped, so a returned job always matches —
            # guard it as belt-and-suspenders before adopting. A plain `assert`
            # would be stripped under `python -O`, exactly where the guard matters.
            if existing.transport != body.transport:
                raise RuntimeError(
                    f"find_active_for_section returned transport={existing.transport!r} "
                    f"for a transport={body.transport!r} lookup")
            if existing.batch_id is None:
                existing.batch_id = batch.id
                adopted += 1
            else:
                skipped += 1
            continue
        # No active (pending/running/done) job → "remaining". Resume a saved
        # failed/cancelled section instead of discarding it; else create fresh.
        latest = await jobs_repo.latest_for_section(
            session, body.book_id, t.id, transport=body.transport)
        if (latest is not None and latest.status in ("failed", "cancelled")
                and body.relaunch_mode != "discard"):
            await jobs_repo.reset_for_retry(session, latest.id)   # reuses done phases
            resumed += 1
            continue
        # brand-new section, OR discard mode → fresh job (discard leaves the old
        # failed/cancelled row as history; find_active ignores it)
        await jobs_repo.create(session, book_id=body.book_id, toc_entry_id=t.id,
                               subject=book.subject, provider=provider,
                               model=body.model, batch_id=batch.id,
                               transport=body.transport,
                               extract_transport=body.extract_transport,
                               judge_transport=body.judge_transport,
                               extract_provider=body.extract_provider,
                               extract_model=body.extract_model,
                               judge_provider=body.judge_provider,
                               judge_model=body.judge_model)
        created += 1

    await session.flush()
    tally = await batches_repo.rollup_for_batch(session, batch.id)
    await session.commit()

    payload = _rollup_payload(batch, tally, book.original_filename)
    payload.update(jobs_created=created, jobs_adopted=adopted,
                   jobs_skipped=skipped, jobs_resumed=resumed)
    return payload


@router.get("/jobs/batches")
async def list_batches(session: AsyncSession = Depends(get_session)):
    rows = await batches_repo.list_with_rollups(session)
    return {"batches": [_rollup_payload(r["batch"], r["rollup"], r.get("original_filename"))
                        for r in rows]}


@router.get("/jobs/batches/{batch_id}")
async def get_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    tally = await batches_repo.rollup_for_batch(session, batch_id)
    book = await books_repo.get(session, batch.book_id)
    return _rollup_payload(batch, tally, book.original_filename if book else None)


@router.post("/jobs/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    counts = await jobs_repo.cancel_all_in_batch(session, batch_id)
    running_ids = await jobs_repo.running_job_ids_in_batch(session, batch_id)
    await session.commit()
    # Instant local kill for any task running in THIS process (others self-cancel
    # via the heartbeat within heartbeat_seconds).
    from app.services.worker import RUNNING_JOBS
    for jid in running_ids:
        task = RUNNING_JOBS.get(jid)
        if task is not None:
            task.cancel()
    return {"batch_id": str(batch_id), **counts}


@router.post("/jobs/batch/{batch_id}/resume")
async def resume_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    resumed = await jobs_repo.resume_failed_in_batch(session, batch_id)
    await session.commit()
    return {"batch_id": str(batch_id), "jobs_resumed": resumed}


@router.get("/jobs/batches/{batch_id}/jobs")
async def list_batch_jobs(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    return {"batch_id": str(batch_id), "jobs": await batches_repo.list_jobs(session, batch_id)}
