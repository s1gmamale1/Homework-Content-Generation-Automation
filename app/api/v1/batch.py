from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.services.agent_models import is_valid, validate_transport

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
    force: bool = False


def _rollup_payload(batch, tally: dict[str, int]) -> dict:
    return {
        "batch_id": str(batch.id),
        "book_id": str(batch.book_id),
        "subject": batch.subject,
        "grade": batch.grade,
        "provider": batch.provider,
        "model": batch.model,
        "transport": batch.transport,
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

    batch = await batches_repo.get_or_create_for_book(
        session, book_id=body.book_id, subject=book.subject, grade=book.grade,
        provider=provider, model=body.model, transport=body.transport)

    created = adopted = skipped = 0
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
        await jobs_repo.create(session, book_id=body.book_id, toc_entry_id=t.id,
                               subject=book.subject, provider=provider,
                               model=body.model, batch_id=batch.id,
                               transport=body.transport)
        created += 1

    await session.flush()
    tally = await batches_repo.rollup_for_batch(session, batch.id)
    await session.commit()

    payload = _rollup_payload(batch, tally)
    payload.update(jobs_created=created, jobs_adopted=adopted, jobs_skipped=skipped)
    return payload


@router.get("/jobs/batches")
async def list_batches(session: AsyncSession = Depends(get_session)):
    rows = await batches_repo.list_with_rollups(session)
    return {"batches": [_rollup_payload(r["batch"], r["rollup"]) for r in rows]}


@router.get("/jobs/batches/{batch_id}")
async def get_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    tally = await batches_repo.rollup_for_batch(session, batch_id)
    return _rollup_payload(batch, tally)


@router.get("/jobs/batches/{batch_id}/jobs")
async def list_batch_jobs(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    return {"batch_id": str(batch_id), "jobs": await batches_repo.list_jobs(session, batch_id)}
