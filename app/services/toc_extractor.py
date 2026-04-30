from __future__ import annotations

from pathlib import Path
from time import perf_counter
from uuid import UUID

from loguru import logger

from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import TOCEntryOut
from app.services import events_bus, gemini


async def run(book_id: UUID, file_path: Path, subject: str) -> None:
    """Background task: upload PDF → extract TOC → persist entries → emit SSE."""
    resource_id = f"book:{book_id}"
    log = logger.bind(book_id=str(book_id), subject=subject)
    t_start = perf_counter()
    size_mb = file_path.stat().st_size / (1024 * 1024) if file_path.exists() else 0

    log.info(
        f"[book {book_id}] toc-extraction starting | subject={subject} "
        f"file={file_path.name} size={size_mb:.2f}MB"
    )

    try:
        await events_bus.publish(resource_id, "status", {"status": "uploading"})

        # 1. Upload to Gemini
        log.info(f"[book {book_id}] step 1/4 uploading to gemini")
        t_upload = perf_counter()
        uploaded_file, expires_at = await gemini.upload_file(file_path, book_id=book_id)
        log.info(
            f"[book {book_id}] step 1/4 upload complete | uri={uploaded_file.uri} "
            f"duration_ms={(perf_counter() - t_upload) * 1000:.0f}"
        )

        # 2. Persist file metadata + flip status to toc_extracting
        async with SessionLocal() as session:
            await books_repo.set_gemini_file(
                session, book_id, file_uri=uploaded_file.uri, expires_at=expires_at
            )
            await books_repo.set_status(session, book_id, "toc_extracting")
            await session.commit()
        log.info(f"[book {book_id}] step 2/4 status=toc_extracting (committed)")
        await events_bus.publish(resource_id, "status", {"status": "toc_extracting"})

        # 3. Ask Gemini for the structured TOC
        log.info(f"[book {book_id}] step 3/4 extracting TOC via gemini")
        t_extract = perf_counter()
        extracted = await gemini.extract_toc(uploaded_file, subject, book_id=book_id)
        log.info(
            f"[book {book_id}] step 3/4 TOC extracted | entries={len(extracted.entries)} "
            f"duration_ms={(perf_counter() - t_extract) * 1000:.0f}"
        )

        # 4. Persist entries + flip status to toc_ready
        async with SessionLocal() as session:
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            await books_repo.set_status(session, book_id, "toc_ready")
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]
        log.info(f"[book {book_id}] step 4/4 entries persisted | count={len(rows)}")

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
        try:
            file_path.unlink(missing_ok=True)
            log.debug(f"[book {book_id}] temp file removed | path={file_path}")
        except Exception as exc:
            log.warning(f"[book {book_id}] failed to remove temp file: {exc}")
