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
from app.services import agent, events_bus, toc_ingest_audit
from app.services.toc_classifier import classify_entries


def _merge_verdict(
    result: "agent.TOCValidationResult | None", audit: toc_ingest_audit.IngestAudit
) -> tuple[str | None, str | None]:
    """Fold the deterministic ingest audit into the vision validator's verdict.

    Two independent checks write one pair of columns, so the rules are explicit:

    * **Detail** always carries both — the audit's findings first (they are
      deterministic and name a concrete row), then the validator's prose.
    * **Verdict** is the validator's, EXCEPT that a blocking audit finding
      forces ``mismatch`` — which is what routes the book to ``toc_review``.
      A blocking finding means "these lessons cannot succeed at any host", and
      that is true whether or not an LLM was asked for a second opinion.
    * The audit runs even when ``toc_validation_enabled=False``: that flag
      gates a *paid vision call*, not a free arithmetic check on rows we just
      wrote. When it is off and the audit found only advisories, the verdict
      column stays ``NULL`` (its documented "not validated" value) while the
      detail still records what ingestion saw — no verdict is invented.
    """
    parts: list[str] = []
    if audit.detail:
        parts.append(audit.detail)
    if result is not None and result.detail:
        parts.append(result.detail)
    detail = "; ".join(parts) or None
    if audit.blocking:
        return "mismatch", detail
    return (result.status if result is not None else None), detail


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

        # Deterministic ingest guards — free, no model call, run on every book.
        # Catches at ingestion the two content defects that used to surface only
        # after a worker had claimed a lesson and fetched the book: an inverted
        # page range (`cannot scope page range 35-34`, 3 lessons) and a scanned
        # PDF with no text layer (`sparse text layer (1 chars/page)`, 12 lessons
        # across 5 hosts re-discovering one bad book). Off-by-one inversions are
        # repaired IN PLACE on `extracted.entries` here, before bulk_create, so
        # the row is never persisted inverted; everything else is surfaced.
        # See app/services/toc_ingest_audit.py for the repair-vs-surface policy.
        audit = toc_ingest_audit.audit_book(extracted.entries, file_path)
        log.info(f"[book {book_id}] ingest audit | {audit.summary}")
        for line in (*audit.blocking, *audit.repairs, *audit.advisory):
            log.warning(f"[book {book_id}] ingest audit: {line}")

        # Soft-gate: run vision validator BEFORE persisting status.
        # Disabled (toc_validation_enabled=False) → result stays None → the
        # verdict column stays NULL unless the ingest audit has something to say.
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

        # One verdict/detail pair from two independent checks (see _merge_verdict).
        verdict, detail = _merge_verdict(result, audit)

        # Persist entries + flip status (toc_review on mismatch, toc_ready otherwise)
        async with SessionLocal() as session:
            # Clear-before-insert so a re-extract replaces rather than appends
            # (bulk_create is a naive append; toc_entries has no unique
            # constraint). The re-extract entrypoint (POST /books/{id}/toc/retry)
            # refuses upstream with a 409 when any homework_jobs row references
            # this book's TOC, so this DELETE never hits the toc_entry_id FK for
            # a book with jobs (WISHLIST toc-reextract-fk-blocked-1). A brand-new
            # book (ingest_pdf) has no jobs yet.
            await toc_repo.delete_for_book(session, book_id)
            rows = await toc_repo.bulk_create(session, book_id, extracted.entries)
            final_status = "toc_review" if verdict == "mismatch" else "toc_ready"
            await books_repo.set_status(session, book_id, final_status)
            if final_status == "toc_ready":
                await books_repo.set_toc_ready_at(session, book_id)
            # Write the audit trail whenever EITHER check produced something.
            # A clean book with the vision gate disabled still writes nothing.
            if verdict is not None or detail is not None:
                await books_repo.set_toc_validation(session, book_id, verdict, detail)
            await session.commit()
            entries_out = [TOCEntryOut.model_validate(r) for r in rows]
            # Enrich with entry_class so the live SSE push matches the REST
            # read path (app.api.v1.books._enriched_toc_entries) instead of
            # emitting entry_class: null on every row.
            classes = classify_entries(rows)
            for eo, cls in zip(entries_out, classes):
                eo.entry_class = cls
        log.info(
            f"[book {book_id}] entries persisted | count={len(rows)}"
        )
        log.info(
            f"[book {book_id}] toc validation: "
            f"{result.status if result else 'disabled'} | ingest audit: "
            f"{'blocking' if audit.blocking else ('findings' if audit.has_findings else 'clean')} "
            f"→ status={final_status}"
        )

        # Refetch invariant (events_bus): entries are committed above BEFORE
        # these publishes — an oversized payload's __refetch__ marker makes
        # the SSE endpoint re-read them. Do not reorder publish before commit.
        if final_status == "toc_review":
            # `result` can legitimately be None here — a blocking ingest finding
            # routes to review on its own, with the vision validator disabled or
            # skipped — so the payload is built from the merged verdict, not from
            # `result.status` (which would AttributeError).
            await events_bus.publish(
                resource_id,
                "toc_review",
                {
                    "entries": [e.model_dump(mode="json") for e in entries_out],
                    "validation": {
                        "verdict": verdict,
                        "issues": [
                            *(result.issues if result is not None else []),
                            *audit.blocking,
                            *audit.repairs,
                            *audit.advisory,
                        ],
                    },
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
