import asyncio
import io
import json
import zipfile
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth import get_current_user
from app.db import SessionLocal, get_session
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import GenerateRequest, JobOut, PhaseOut
from app.services import events_bus, pipeline

router = APIRouter(tags=["jobs"])


@router.post("/books/{book_id}/sections/{toc_entry_id}/generate", status_code=201)
async def generate(
    book_id: UUID,
    toc_entry_id: UUID,
    response: Response,
    body: GenerateRequest = GenerateRequest(),
    session: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
) -> JobOut:
    book = await books_repo.get(session, book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")
    section = await toc_repo.get(session, toc_entry_id)
    if section is None or section.book_id != book_id:
        raise HTTPException(404, "section not found")

    if not body.force:
        existing = await jobs_repo.find_active_for_section(session, book_id, toc_entry_id)
        if existing is not None:
            response.status_code = 200
            return await _job_out(session, existing.id)

    job = await jobs_repo.create(
        session,
        book_id=book_id,
        toc_entry_id=toc_entry_id,
        subject=book.subject,
        status="pending",
    )
    await session.commit()

    asyncio.create_task(pipeline.run(job.id))
    return await _job_out(session, job.id)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobOut:
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

    # Default: zip bundle
    games_payload = job.games_json or {"games": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("homework.md", job.assembled_md)
        zf.writestr("games.json", json.dumps(games_payload, ensure_ascii=False, indent=2))
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
