from __future__ import annotations

from pathlib import Path
from time import perf_counter
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import launch_defaults as launch_defaults_repo
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
            ld = await launch_defaults_repo.get(session)
            toc_provider = ld.extract_provider
            toc_model = ld.extract_model
            toc_transport = ld.toc_transport
            await session.commit()
        log.info(f"[book {book_id}] status=toc_extracting (committed)")
        await events_bus.publish(resource_id, "status", {"status": "toc_extracting"})

        # Ask the agent for the structured TOC. The provider/model are sourced
        # from the launch_defaults DB row (extract_provider / extract_model) so
        # the operator can change the cheap-extractor choice without a deploy.
        # The transport comes from launch_defaults.toc_transport (dedicated TOC
        # column, separate from extract_transport which governs lesson extracts).
        log.info(
            f"[book {book_id}] extracting TOC via agent "
            f"({toc_provider} / {toc_model}) transport={toc_transport}"
        )
        t_extract = perf_counter()
        extracted = await agent.extract_toc(
            provider=toc_provider,
            model=toc_model,
            pdf_path=file_path,
            subject=subject,
            book_id=book_id,
            transport=toc_transport,
        )
        log.info(
            f"[book {book_id}] TOC extracted | entries={len(extracted.entries)} "
            f"duration_ms={(perf_counter() - t_extract) * 1000:.0f}"
        )

        # 0 entries = no usable lessons (scanned/image-only PDF with no text
        # layer, or an unparseable contents page). Marking the book `toc_ready`
        # with an empty lesson list silently shows an empty book and lets
        # /generate produce nothing. Fail loudly via the except path below so the
        # operator sees the reason. (WISHLIST `toc-empty-ready`.)
        if not extracted.entries:
            raise RuntimeError(
                "TOC extraction found 0 lessons. Likely a scanned/image-only PDF "
                "whose contents page falls outside the scanned vision window, or an "
                "unparseable table of contents. If the book is scanned, widen "
                "extract_toc_front_pages / extract_toc_back_pages and re-extract."
            )

        # Soft-gate: run vision validator BEFORE persisting status.
        # Disabled (toc_validation_enabled=False) → result stays None → behaves
        # exactly like today (toc_ready, no toc_validation DB row written).
        result = None
        if settings.toc_validation_enabled:
            result = await agent.validate_toc(
                entries=extracted.entries,
                pdf_path=file_path,
                subject=subject,
                book_id=book_id,
                provider=settings.toc_validation_provider,
                model=settings.toc_validation_model,
                transport=toc_transport,
            )

        # Persist entries + flip status (toc_review on mismatch, toc_ready otherwise)
        async with SessionLocal() as session:
            # Clear-before-insert so a re-extract (POST /books/{id}/toc/retry)
            # replaces rather than appends — bulk_create is a naive append and
            # toc_entries has no unique constraint. Safe because retry excludes
            # `toc_ready`, so no homework_jobs.toc_entry_id FK references these
            # rows (they were never surfaced for generation).
            await toc_repo.delete_for_book(session, book_id)
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            final_status = (
                "toc_review" if (result is not None and result.status == "mismatch")
                else "toc_ready"
            )
            await books_repo.set_status(session, book_id, final_status)
            if result is not None:
                await books_repo.set_toc_validation(
                    session, book_id, result.status, result.detail or None
                )
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]
        log.info(
            f"[book {book_id}] entries persisted | count={len(rows)}"
        )
        log.info(
            f"[book {book_id}] toc validation: "
            f"{result.status if result else 'disabled'} → status={final_status}"
        )

        if final_status == "toc_review":
            await events_bus.publish(
                resource_id,
                "toc_review",
                {
                    "entries": [e.model_dump(mode="json") for e in entries_out],
                    "validation": {"verdict": result.status, "issues": result.issues},
                },
            )
        else:
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
