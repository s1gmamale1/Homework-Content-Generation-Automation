from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Optional
from uuid import UUID

from loguru import logger

from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import toc_entries as toc_repo
from app.services import events_bus, gemini
from app.services.flows import (
    SUBJECT_FLOWS,
    file_needed_phases,
    filter_prior_outputs,
)
from app.services.prompts import get_prompt, get_prompt_hash

_INTERNAL_PHASES = {"extract", "classify"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run(job_id: UUID) -> None:
    """Execute a homework job: extract → classify? → content phases → assemble."""
    resource_id = f"job:{job_id}"
    log = logger.bind(job_id=str(job_id))
    t_start = perf_counter()

    log.info(f"[job {job_id}] pipeline starting")

    # Hoisted above try/except so the `finally` block can clean it up even if
    # an exception fires before the phase loop assigns it.
    cache_name: Optional[str] = None

    try:
        # ─── load job + book + section ─────────────────────────
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
                log.warning(f"[job {job_id}] not found, aborting")
                return
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None or book.gemini_file_uri is None:
                raise RuntimeError("Job is missing book or section context")
            subject = book.subject
            file_uri = book.gemini_file_uri
            book_id = book.id
            book_cache_name = book.gemini_cache_name
            book_cache_expires_at = book.gemini_cache_expires_at
            section_data = {
                "id": section.id,
                "title": section.section_title,
                "number": section.section_number,
                "page_start": section.page_start,
                "page_end": section.page_end,
            }
        log.info(
            f"[job {job_id}] context loaded | subject={subject} "
            f"section={section_data['number']!r} title={section_data['title']!r} "
            f"pages={section_data['page_start']}-{section_data['page_end']}"
        )

        # ─── plan phase sequence ───────────────────────────────
        flow = SUBJECT_FLOWS[subject]
        sequence: list[str] = ["extract"]
        if flow["has_classify"]:
            sequence.append("classify")
        else:
            sequence.extend(flow["hard"])
        log.info(
            f"[job {job_id}] sequence planned | has_classify={flow['has_classify']} "
            f"initial_phases={sequence}"
        )

        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "running", started_at=_utcnow())
            await session.commit()

        difficulty: Optional[str] = None
        prior_outputs: dict[str, str] = {}
        lesson_context: Optional[str] = None
        phase_order = 0

        file_phases = file_needed_phases(subject)
        log.info(
            f"[job {job_id}] file-needed phases for '{subject}': "
            f"{sorted(file_phases) or '(none beyond extract)'}"
        )

        # Per-book context cache: get-or-create. Persists on the books row so
        # subsequent jobs against the same book skip re-paying for the PDF.
        # Used by the extract phase (always) and any content phase that opts
        # into PHASE_FILE_NEEDED.
        cache_name = await _ensure_book_cache(
            book_id=book_id,
            file_uri=file_uri,
            existing_name=book_cache_name,
            existing_expires_at=book_cache_expires_at,
            log=log,
        )

        # ─── phase loop ────────────────────────────────────────
        while phase_order < len(sequence):
            phase_name = sequence[phase_order]
            log.info(
                f"[job {job_id}] phase {phase_order + 1}/{len(sequence)} "
                f"'{phase_name}' starting"
            )
            t_phase = perf_counter()
            await _emit_started(resource_id, phase_name, phase_order)

            # extract reads the PDF; content phases skip the file unless the
            # subject opted them into PHASE_FILE_NEEDED. Either way, when a
            # phase touches the file we route through the per-book cache.
            phase_needs_file = phase_name == "extract" or phase_name in file_phases

            # Trim prior_outputs to what this phase declared as deps.
            phase_prior = filter_prior_outputs(phase_name, prior_outputs)

            try:
                output_md, tin, tout, _ph = await _execute_phase(
                    job_id=job_id,
                    phase_name=phase_name,
                    phase_order=phase_order,
                    subject=subject,
                    file_uri=file_uri,
                    attach_file=phase_needs_file,
                    cached_content=cache_name if phase_needs_file else None,
                    section=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=phase_prior,
                    difficulty=difficulty,
                )
            except Exception as exc:
                phase_ms = (perf_counter() - t_phase) * 1000
                log.exception(
                    f"[job {job_id}] phase '{phase_name}' FAILED after {phase_ms:.0f}ms: {exc}"
                )
                async with SessionLocal() as session:
                    await jobs_repo.set_status(
                        session, job_id, "failed",
                        completed_at=_utcnow(),
                        error_message=f"{phase_name}: {exc}",
                    )
                    await session.commit()
                await events_bus.publish(
                    resource_id, "error", {"phase_name": phase_name, "message": str(exc)}
                )
                return

            phase_ms = (perf_counter() - t_phase) * 1000
            log.success(
                f"[job {job_id}] phase '{phase_name}' done | "
                f"output_chars={len(output_md)} tokens_in={tin} tokens_out={tout} "
                f"duration_ms={phase_ms:.0f}"
            )

            await events_bus.publish(
                resource_id,
                "phase_completed",
                {
                    "phase_name": phase_name,
                    "phase_order": phase_order,
                    "output_md": output_md,
                    "tokens_input": tin,
                    "tokens_output": tout,
                },
            )

            # phase-specific follow-up actions
            if phase_name == "extract":
                lesson_context = output_md
                log.info(f"[job {job_id}] lesson_context captured | chars={len(output_md)}")
            elif phase_name == "classify":
                difficulty = _parse_classify(output_md)
                async with SessionLocal() as session:
                    await jobs_repo.set_difficulty(session, job_id, difficulty)
                    await session.commit()
                await events_bus.publish(
                    resource_id, "difficulty_classified", {"difficulty": difficulty}
                )
                appended = flow[difficulty]
                sequence.extend(appended)
                log.info(
                    f"[job {job_id}] difficulty resolved={difficulty} | "
                    f"appended_phases={appended} new_total={len(sequence)}"
                )
            else:
                prior_outputs[phase_name] = output_md

            phase_order += 1

        # ─── assembly ──────────────────────────────────────────
        log.info(f"[job {job_id}] assembling homework markdown")
        assembled = await _assemble(job_id)

        # ─── games + flashcards extraction ─────────────────────
        # Pull the raw phase outputs and re-parse them as structured JSON via
        # Gemini's response_schema. Stored on the job so the frontend can
        # render interactive game cards and a flippable flashcard deck.
        game_breaks_md = prior_outputs.get("game-breaks", "")
        flashcards_md = prior_outputs.get("flashcards", "")

        games_json: Optional[dict] = None
        if game_breaks_md.strip():
            log.info(f"[job {job_id}] extracting games from game-breaks output")
            games_pack = await gemini.extract_games(
                game_breaks_md, homework_job_id=job_id
            )
            if games_pack is not None:
                games_json = games_pack.model_dump(mode="json")
                log.info(
                    f"[job {job_id}] games extracted | count={len(games_pack.games)} "
                    f"types={[g.type for g in games_pack.games]}"
                )

        flashcards_json: Optional[dict] = None
        if flashcards_md.strip():
            log.info(f"[job {job_id}] extracting flashcards")
            flashcards_pack = await gemini.extract_flashcards(
                flashcards_md, homework_job_id=job_id
            )
            if flashcards_pack is not None:
                flashcards_json = flashcards_pack.model_dump(mode="json")
                log.info(
                    f"[job {job_id}] flashcards extracted | count={len(flashcards_pack.cards)}"
                )

        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "done",
                completed_at=_utcnow(),
                assembled_md=assembled,
            )
            if games_json is not None:
                await jobs_repo.set_games_json(session, job_id, games_json)
            if flashcards_json is not None:
                await jobs_repo.set_flashcards_json(session, job_id, flashcards_json)
            await session.commit()

        await events_bus.publish(
            resource_id,
            "job_completed",
            {"job_id": str(job_id), "download_url": f"/api/v1/jobs/{job_id}/download"},
        )

        total_s = perf_counter() - t_start
        log.success(
            f"[job {job_id}] pipeline complete | phases_run={len(sequence)} "
            f"assembled_chars={len(assembled)} total_s={total_s:.1f}"
        )
        await _log_token_summary(job_id, log)

    except Exception as exc:
        total_s = perf_counter() - t_start
        log.exception(
            f"[job {job_id}] pipeline CRASHED after {total_s:.1f}s: {exc}"
        )
        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
            )
            await session.commit()
        await events_bus.publish(resource_id, "error", {"message": str(exc)})
    finally:
        await events_bus.close(resource_id)
        # Note: cache_name is the per-BOOK cache. We do NOT delete it here —
        # it lives on the books row so subsequent jobs against the same book
        # reuse it. Gemini auto-expires the cache on its own TTL.


async def _ensure_book_cache(
    *,
    book_id: UUID,
    file_uri: str,
    existing_name: Optional[str],
    existing_expires_at: Optional[datetime],
    log,
) -> Optional[str]:
    """Get-or-create a per-book Gemini context cache.

    Reuses the existing cache if it has > 5 minutes left. Otherwise creates a
    fresh cache and persists name + expiry on the books row. Returns None on
    creation failure (caller falls back to inline file Part)."""
    now = datetime.now(timezone.utc)
    refresh_threshold = now + timedelta(minutes=5)

    if existing_name and existing_expires_at:
        # Naive vs aware datetime: ensure aware comparison.
        existing_aware = existing_expires_at
        if existing_aware.tzinfo is None:
            existing_aware = existing_aware.replace(tzinfo=timezone.utc)
        if existing_aware > refresh_threshold:
            ttl_remaining = existing_aware - now
            log.info(
                f"[book {book_id}] reusing context cache | name={existing_name} "
                f"ttl_remaining={ttl_remaining}"
            )
            return existing_name
        log.info(
            f"[book {book_id}] cache expired/expiring | name={existing_name} "
            f"expires_at={existing_aware.isoformat()}"
        )

    log.info(f"[book {book_id}] creating per-book context cache for {file_uri}")
    result = await gemini.create_cache(file_uri=file_uri)
    if result is None:
        log.warning(f"[book {book_id}] cache creation failed; falling back to inline file")
        return None

    cache_name, expire_time = result
    async with SessionLocal() as session:
        await books_repo.set_gemini_cache(
            session,
            book_id,
            cache_name=cache_name,
            expires_at=expire_time,
        )
        await session.commit()

    log.success(
        f"[book {book_id}] cache stored | name={cache_name} "
        f"expires_at={expire_time.isoformat()}"
    )
    return cache_name


async def _emit_started(resource_id: str, phase_name: str, phase_order: int) -> None:
    await events_bus.publish(
        resource_id,
        "phase_started",
        {"phase_name": phase_name, "phase_order": phase_order},
    )


async def _execute_phase(
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    subject: str,
    file_uri: str,
    attach_file: bool = False,
    cached_content: Optional[str] = None,
    section: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int], str]:
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v1"
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)

    async with SessionLocal() as session:
        po = await phase_repo.create(
            session,
            job_id=job_id,
            phase_name=phase_name,
            phase_order=phase_order,
            prompt_hash=prompt_hash,
            model_name=settings.gemini_model,
        )
        await phase_repo.set_status(session, po.id, "running", started_at=_utcnow())
        await jobs_repo.set_status(session, job_id, "running", current_phase=phase_name)
        await session.commit()
        po_id = po.id

    logger.debug(
        f"[job {job_id}] phase row created | phase={phase_name} order={phase_order} "
        f"prompt_hash={prompt_hash[:12]} model={settings.gemini_model}"
    )

    try:
        if phase_name == "extract":
            # Cross-job cache: if we've already extracted this section under
            # the current builtin extract prompt, reuse the prior output and
            # skip Gemini entirely. Saves ~15s + ~1.5K output tokens per
            # regeneration / repeat job on the same section.
            cached_extract = None
            section_id = section.get("id")
            if section_id is not None:
                async with SessionLocal() as session:
                    cached_extract = await phase_repo.find_latest_extract(
                        session,
                        toc_entry_id=section_id,
                        prompt_hash=prompt_hash,
                    )

            if cached_extract is not None and cached_extract.output_md:
                logger.info(
                    f"[job {job_id}] lesson.extract REUSED from job={cached_extract.job_id} "
                    f"po={cached_extract.id} (skipping gemini call)"
                )
                async with SessionLocal() as session:
                    await phase_repo.set_status(
                        session,
                        po_id,
                        "done",
                        completed_at=_utcnow(),
                        output_md=cached_extract.output_md,
                        tokens_input=0,
                        tokens_output=0,
                    )
                    await session.commit()
                # Visibility: record a free gemini_usages row
                await gemini.record_cached_lesson_extract(
                    homework_job_id=job_id,
                    phase_output_id=po_id,
                    source_job_id=cached_extract.job_id,
                    source_phase_output_id=cached_extract.id,
                )
                return cached_extract.output_md, 0, 0, prompt_hash

            output_md, tin, tout = await gemini.extract_lesson_context(
                file_uri=file_uri,
                section_title=section["title"],
                section_number=section["number"],
                page_start=section["page_start"],
                page_end=section["page_end"],
                cached_content=cached_content,
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await gemini.run_phase_prompt(
                phase_prompt=phase_prompt,
                file_uri=file_uri if attach_file else None,
                attach_file=attach_file,
                cached_content=cached_content,
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
                phase_name=phase_name,
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
    except Exception as exc:
        async with SessionLocal() as session:
            await phase_repo.set_status(
                session, po_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
            )
            await session.commit()
        raise

    async with SessionLocal() as session:
        await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md=output_md,
            tokens_input=tin,
            tokens_output=tout,
        )
        await session.commit()

    return output_md, tin, tout, prompt_hash


def _parse_classify(output_md: str) -> str:
    upper = output_md.upper()
    if "HARD" in upper:
        return "hard"
    return "easy"


async def _assemble(job_id: UUID) -> str:
    async with SessionLocal() as session:
        phases = await phase_repo.list_for_job(session, job_id)
    parts: list[str] = []
    for p in phases:
        if p.phase_name in _INTERNAL_PHASES:
            continue
        title = p.phase_name.replace("-", " ").title()
        body = p.output_md or "(empty)"
        parts.append(f"## {title}\n\n{body}\n")
    return "\n".join(parts)


async def _log_token_summary(job_id: UUID, log) -> None:
    """End-of-pipeline summary: per-call token cost as a flat ASCII table.

    Renders one row per gemini_usages row for this job (plus a TOTAL footer)
    so the optimizations are immediately verifiable from the terminal — large
    inputs on `extract` only, small inputs on every other phase means the PDF
    skip is working; non-zero `cached` column means context caching landed.

    Reads token counts from `usage_metadata` (the raw SDK dump) rather than
    the per-modality columns, because PDF tokens are reported under the IMAGE
    modality — they're invisible if you only look at `input_text_token_count`.
    """
    from sqlalchemy import select  # local import: only used here

    from app.models import GeminiUsage

    async with SessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(GeminiUsage)
                    .where(GeminiUsage.homework_job_id == job_id)
                    .order_by(GeminiUsage.created_at)
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        return

    OP_W = 28
    header = (
        f"{'operation':<{OP_W}}"
        f"{'input':>10}{'cached':>10}{'fresh':>10}{'out':>9}{'dur':>9}  file ok"
    )
    bar = "─" * len(header)
    lines = [bar, header, bar]

    total_in = total_out = total_cached = total_image = 0
    for r in rows:
        meta = r.usage_metadata or {}
        # Truth lives in the SDK dump; columns only capture text+audio modalities.
        prompt_in = int(meta.get("prompt_token_count") or 0) or r.input_text_token_count
        cached = int(meta.get("cached_content_token_count") or 0)
        out_tokens = int(meta.get("candidates_token_count") or 0) or r.candidates_token_count
        fresh_in = max(prompt_in - cached, 0)

        # PDF tokens are reported under modality=IMAGE.
        image_tokens = 0
        for d in meta.get("prompt_tokens_details") or []:
            if (d or {}).get("modality") == "IMAGE":
                image_tokens += int(d.get("token_count") or 0)
        file_marker = "PDF" if image_tokens > 0 else "—"

        ok = "✓" if r.success else "✗"
        op_label = r.operation
        if isinstance(meta.get("phase_name"), str):
            op_label = f"{r.operation}:{meta['phase_name']}"
        if len(op_label) > OP_W - 1:
            op_label = op_label[: OP_W - 2] + "…"

        lines.append(
            f"{op_label:<{OP_W}}"
            f"{prompt_in:>10,}"
            f"{cached:>10,}"
            f"{fresh_in:>10,}"
            f"{out_tokens:>9,}"
            f"{(r.duration or '—'):>9}"
            f"  {file_marker:<3} {ok}"
        )
        total_in += prompt_in
        total_out += out_tokens
        total_cached += cached
        total_image += image_tokens

    fresh_total = max(total_in - total_cached, 0)
    cache_pct = (total_cached / total_in * 100) if total_in else 0
    pdf_calls = sum(
        1
        for r in rows
        if any(
            (d or {}).get("modality") == "IMAGE"
            for d in (r.usage_metadata or {}).get("prompt_tokens_details") or []
        )
    )

    lines.append(bar)
    lines.append(
        f"{'TOTAL':<{OP_W}}"
        f"{total_in:>10,}"
        f"{total_cached:>10,}"
        f"{fresh_total:>10,}"
        f"{total_out:>9,}"
        f"{'':>9}"
    )
    lines.append(
        f"  {len(rows)} calls · {pdf_calls} attached the PDF · "
        f"PDF tokens total: {total_image:,} · "
        f"cache hit (gemini implicit + explicit): {cache_pct:.0f}% · "
        f"net billed input (fresh): {fresh_total:,}"
    )
    lines.append(bar)

    log.info(f"[job {job_id}] token summary\n" + "\n".join(lines))
