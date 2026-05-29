from __future__ import annotations

from pathlib import Path
from time import perf_counter
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import TOCEntryOut
from app.services import agent, events_bus


async def run(book_id: UUID, file_path: Path, subject: str) -> None:
    """Background task: extract TOC from on-disk PDF → persist entries → emit SSE.

    The PDF stays at ``file_path`` for the lifetime of the book — every later
    phase (lesson extract, content phases) re-attaches it via the agent CLI.
    No temp-file cleanup happens here.
    """
    resource_id = f"book:{book_id}"
    log = logger.bind(book_id=str(book_id), subject=subject)
    t_start = perf_counter()
    size_mb = file_path.stat().st_size / (1024 * 1024) if file_path.exists() else 0

    log.info(
        f"[book {book_id}] toc-extraction starting | subject={subject} "
        f"file={file_path.name} size={size_mb:.2f}MB"
    )

    try:
        # Flip status to toc_extracting and emit SSE so the frontend can show
        # the extraction spinner. The PDF is already persisted on disk by the
        # API handler; no upload step is required.
        async with SessionLocal() as session:
            await books_repo.set_status(session, book_id, "toc_extracting")
            await session.commit()
        log.info(f"[book {book_id}] status=toc_extracting (committed)")
        await events_bus.publish(resource_id, "status", {"status": "toc_extracting"})

        # Ask the agent for the structured TOC. The provider/model are pinned
        # to the cheap-extractor settings (gemini-flash by default) regardless
        # of any per-job choice — TOC extraction is a one-shot factual read
        # that doesn't benefit from a smart-tier model.
        log.info(
            f"[book {book_id}] extracting TOC via agent "
            f"({settings.extract_provider} / {settings.extract_model})"
        )
        t_extract = perf_counter()
        extracted = await agent.extract_toc(
            provider=settings.extract_provider,
            model=settings.extract_model,
            pdf_path=file_path,
            subject=subject,
            book_id=book_id,
        )
        log.info(
            f"[book {book_id}] TOC extracted | entries={len(extracted.entries)} "
            f"duration_ms={(perf_counter() - t_extract) * 1000:.0f}"
        )

        # Persist entries + flip status to toc_ready
        async with SessionLocal() as session:
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            await books_repo.set_status(session, book_id, "toc_ready")
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]
        log.info(f"[book {book_id}] entries persisted | count={len(rows)}")

        await events_bus.publish(
            resource_id,
            "toc_ready",
            {"entries": [e.model_dump(mode="json") for e in entries_out]},
        )

        total_ms = (perf_counter() - t_start) * 1000
        log.success(
            f"[book {book_id}] toc-extraction complete | entries={len(rows)} "
            f"total_ms={total_ms:.0f}"
        )

    except Exception as exc:
        total_ms = (perf_counter() - t_start) * 1000
        log.exception(
            f"[book {book_id}] toc-extraction FAILED after {total_ms:.0f}ms: {exc}"
        )
        async with SessionLocal() as session:
            await books_repo.set_status(session, book_id, "failed", error_message=str(exc))
            await session.commit()
        await events_bus.publish(resource_id, "error", {"message": str(exc)})

    finally:
        await events_bus.close(resource_id)
        # NOTE: The PDF is intentionally left on disk — every subsequent phase
        # (lesson.extract, content phases that opt-in to attachments) reads it.
