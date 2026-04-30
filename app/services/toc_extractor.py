import logging
from pathlib import Path
from uuid import UUID

from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import toc_entries as toc_repo
from app.schemas import TOCEntryOut
from app.services import events_bus, gemini

log = logging.getLogger(__name__)


async def run(book_id: UUID, file_path: Path, subject: str) -> None:
    resource_id = f"book:{book_id}"
    try:
        await events_bus.publish(resource_id, "status", {"status": "uploading"})

        file_uri, expires_at = await gemini.upload_file(file_path)

        async with SessionLocal() as session:
            await books_repo.set_gemini_file(
                session, book_id, file_uri=file_uri, expires_at=expires_at
            )
            await books_repo.set_status(session, book_id, "toc_extracting")
            await session.commit()

        await events_bus.publish(resource_id, "status", {"status": "toc_extracting"})

        extracted = await gemini.extract_toc(file_uri, subject)

        async with SessionLocal() as session:
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            await books_repo.set_status(session, book_id, "toc_ready")
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]

        await events_bus.publish(
            resource_id,
            "toc_ready",
            {"entries": [e.model_dump(mode="json") for e in entries_out]},
        )
    except Exception as exc:
        log.exception("TOC extraction failed for book %s", book_id)
        async with SessionLocal() as session:
            await books_repo.set_status(session, book_id, "failed", error_message=str(exc))
            await session.commit()
        await events_bus.publish(resource_id, "error", {"message": str(exc)})
    finally:
        await events_bus.close(resource_id)
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass
