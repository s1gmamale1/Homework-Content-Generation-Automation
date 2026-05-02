from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Optional
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
    max_output_tokens_for,
    resolve_phase_deps,
)
from app.services.prompts import get_prompt, get_prompt_hash

_INTERNAL_PHASES = {"extract", "classify"}


def _synth_md_for_structured(phase_name: str, parsed: Any) -> str:
    """Render a tiny human-readable Markdown body from structured JSON output.

    Used as `output_md` for structured phases — shown in /job/:id phase rows
    and bundled into homework.md inside the download ZIP. Interactive renders
    on /preview/:id read from the matching *_json column instead, so this is
    purely for "I want to read the homework as a document" use cases.
    """
    if phase_name == "classify":
        difficulty = getattr(parsed, "difficulty", "?")
        reason = getattr(parsed, "reason", "") or ""
        return f"**Classification:** {difficulty.upper()}" + (f" — {reason}" if reason else "")

    if phase_name == "flashcards":
        cards = getattr(parsed, "cards", None) or []
        out = [f"_{len(cards)} flashcards — interactive deck rendered in preview._\n"]
        for i, c in enumerate(cards, 1):
            out.append(f"{i}. **{c.front}** — {c.back}")
            if getattr(c, "hint", None):
                out.append(f"   - hint: {c.hint}")
        return "\n".join(out)

    if phase_name == "memory-sprint":
        items = getattr(parsed, "items", None) or []
        out = [f"_{len(items)} rapid-fire items — interactive sprint rendered in preview._\n"]
        for i, it in enumerate(items, 1):
            out.append(f"{i}. **[{it.kind.upper()}]** {it.prompt}")
            for j, opt in enumerate(it.options or []):
                marker = "✓" if j == it.correct_index else " "
                out.append(f"   - [{marker}] {opt}")
            if getattr(it, "explanation", None):
                out.append(f"   - _{it.explanation}_")
        return "\n".join(out)

    if phase_name == "game-breaks":
        games = getattr(parsed, "games", None) or []
        out = [f"_{len(games)} game breaks — interactive cards rendered in preview._\n"]
        for i, g in enumerate(games, 1):
            count = len(getattr(g, "questions", None) or getattr(g, "pairs", None)
                        or getattr(g, "cards", None) or [])
            out.append(f"{i}. **{g.title}** _({g.type}, {count} items)_")
        return "\n".join(out)

    if phase_name == "final-challenge":
        qs = getattr(parsed, "questions", None) or []
        title = getattr(parsed, "title", None) or "Final Challenge"
        hp = getattr(parsed, "starting_hp", 100)
        out = [
            f"_{title} — boss fight with {len(qs)} questions, "
            f"starting HP {hp}. Interactive battle rendered in preview._\n"
        ]
        for i, q in enumerate(qs, 1):
            out.append(f"{i}. **[{q.kind.upper()} · -{q.damage} HP]** {q.prompt}")
            if q.options:
                for j, opt in enumerate(q.options):
                    marker = "✓" if q.correct_index is not None and j == q.correct_index else " "
                    out.append(f"   - [{marker}] {opt}")
            elif getattr(q, "correct_answer", None):
                out.append(f"   - answer: {q.correct_answer}")
            if getattr(q, "explanation", None):
                out.append(f"   - _{q.explanation}_")
        return "\n".join(out)

    if phase_name == "reading":
        cps = getattr(parsed, "checkpoints", None) or []
        cefr = f" (CEFR {parsed.cefr_level})" if getattr(parsed, "cefr_level", None) else ""
        passage = getattr(parsed, "passage_md", "") or ""
        out = [
            f"_Reading passage{cefr} with {len(cps)} comprehension checkpoints. "
            f"Interactive reader rendered in preview._\n",
            passage,
        ]
        for i, cp in enumerate(cps, 1):
            after = (cp.after_paragraph or 0) + 1
            out.append(f"\n**Checkpoint {i}** _(after paragraph {after})_  ")
            out.append(cp.prompt)
            if cp.options:
                for j, opt in enumerate(cp.options):
                    marker = "✓" if cp.correct_index is not None and j == cp.correct_index else " "
                    out.append(f"- [{marker}] {opt}")
            elif getattr(cp, "correct_answer", None):
                out.append(f"- answer: {cp.correct_answer}")
            if getattr(cp, "explanation", None):
                out.append(f"  _{cp.explanation}_")
        return "\n".join(out)

    return ""


_JSON_COLUMN_SETTERS = {
    "flashcards": jobs_repo.set_flashcards_json,
    "memory-sprint": jobs_repo.set_memory_sprint_json,
    "game-breaks": jobs_repo.set_games_json,
    "final-challenge": jobs_repo.set_final_challenge_json,
    "reading": jobs_repo.set_reading_json,
}


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

        # ─── head: extract + classify (sequential — everyone depends on them) ──
        # Each step is sequential because the next step's content depends on
        # this one's *output*: extract → lesson_context → classify → difficulty.
        head_phases: list[str] = ["extract"]
        if flow["has_classify"]:
            head_phases.append("classify")

        for idx, phase_name in enumerate(head_phases):
            try:
                output_md, _tin, _tout, _parsed = await _execute_one_phase(
                    job_id=job_id,
                    resource_id=resource_id,
                    log=log,
                    phase_name=phase_name,
                    phase_order=idx,
                    total_phases_hint=len(sequence),
                    subject=subject,
                    file_uri=file_uri,
                    file_phases=file_phases,
                    book_cache_name=cache_name,
                    section_data=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                )
            except Exception:
                # _execute_one_phase already published the error event and
                # marked the job failed. We just unwind cleanly.
                return

            if phase_name == "extract":
                lesson_context = output_md
                log.info(f"[job {job_id}] lesson_context captured | chars={len(output_md)}")
            elif phase_name == "classify":
                # Schema-constrained classifier returns ClassifyDecision; the
                # Literal[easy,hard] enum guarantees `difficulty` is one of
                # those two strings. No substring matching, no defaulting.
                if hasattr(_parsed, "difficulty"):
                    difficulty = _parsed.difficulty
                else:
                    # Defensive fallback for any future case where structured
                    # routing didn't fire (e.g., schema removed from map).
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

        content_phases = sequence[len(head_phases):]

        # ─── per-job text context cache (best-effort) ─────────────────────────
        # Bundle lesson_context + universal directives into a Gemini cache so
        # content phases reference them at ~25% input rate. Falls back to inline
        # if the model rejects (commonly because payload < min cache size).
        job_text_cache_name: Optional[str] = None
        if lesson_context and content_phases:
            cache_payload = (
                "## Lesson context\n" + lesson_context + "\n"
                + gemini._SVG_RULES.lstrip() + "\n"
                + gemini._NO_PREAMBLE
            )
            job_text_cache_name = await gemini.create_text_cache(text=cache_payload)
            if job_text_cache_name:
                log.info(
                    f"[job {job_id}] per-job text cache active | name={job_text_cache_name} "
                    f"chars={len(cache_payload)}"
                )

        # ─── tail: content phases (parallel, wave-based by PHASE_DEPS) ────────
        # Everything from sequence[len(head_phases):] is a content phase. They
        # run concurrently when their PHASE_DEPS are satisfied — typically a 2x
        # speedup over the old sequential loop.
        if content_phases:
            try:
                await _run_content_phases_parallel(
                    job_id=job_id,
                    resource_id=resource_id,
                    log=log,
                    content_phases=content_phases,
                    phase_order_offset=len(head_phases),
                    subject=subject,
                    file_uri=file_uri,
                    file_phases=file_phases,
                    book_cache_name=cache_name,
                    job_text_cache_name=job_text_cache_name,
                    section_data=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                )
            except RuntimeError as exc:
                if "content phase failed" in str(exc):
                    # _execute_one_phase already published the error and marked
                    # the job failed. Unwind cleanly without overwriting state.
                    return
                raise

        # ─── assembly ──────────────────────────────────────────
        log.info(f"[job {job_id}] assembling homework markdown")
        assembled = await _assemble(job_id)

        async with SessionLocal() as session:
            await jobs_repo.set_status(
                session, job_id, "done",
                completed_at=_utcnow(),
                assembled_md=assembled,
            )
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


async def _execute_one_phase(
    *,
    job_id: UUID,
    resource_id: str,
    log,
    phase_name: str,
    phase_order: int,
    total_phases_hint: int,
    subject: str,
    file_uri: str,
    file_phases: set[str],
    book_cache_name: Optional[str],
    job_text_cache_name: Optional[str] = None,
    section_data: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int], Optional[Any]]:
    """Run a single phase end-to-end with status tracking, SSE emit, and
    error handling. Wraps `_execute_phase` so both the sequential head loop
    and the parallel content-phase scheduler share identical lifecycle code.

    On exception, marks the job failed, publishes an error event, and re-raises
    so the caller can short-circuit.
    """
    log.info(
        f"[job {job_id}] phase {phase_order + 1}/{total_phases_hint} "
        f"'{phase_name}' starting"
    )
    t_phase = perf_counter()
    await _emit_started(resource_id, phase_name, phase_order)

    phase_needs_file = phase_name == "extract" or phase_name in file_phases
    phase_prior = filter_prior_outputs(phase_name, prior_outputs)

    # Cache choice: PDF cache wins when the phase actually reads the file;
    # otherwise use the per-job text cache (lesson_context + universal
    # directives) if it exists. If neither, content runs inline.
    if phase_needs_file:
        cached_content = book_cache_name
        cache_holds_text_context = False
    elif job_text_cache_name and phase_name not in _INTERNAL_PHASES:
        cached_content = job_text_cache_name
        cache_holds_text_context = True
    else:
        cached_content = None
        cache_holds_text_context = False

    try:
        output_md, tin, tout, _ph, parsed_struct = await _execute_phase(
            job_id=job_id,
            phase_name=phase_name,
            phase_order=phase_order,
            subject=subject,
            file_uri=file_uri,
            attach_file=phase_needs_file,
            cached_content=cached_content,
            cache_holds_text_context=cache_holds_text_context,
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
        raise

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
    return output_md, tin, tout, parsed_struct


async def _run_content_phases_parallel(
    *,
    job_id: UUID,
    resource_id: str,
    log,
    content_phases: list[str],
    phase_order_offset: int,
    subject: str,
    file_uri: str,
    file_phases: set[str],
    book_cache_name: Optional[str],
    job_text_cache_name: Optional[str] = None,
    section_data: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> None:
    """Wave-based parallel scheduler for content phases.

    Each phase declares its deps in `flows.PHASE_DEPS`. We launch every phase
    whose deps are satisfied, then wait for the next completion, update
    prior_outputs, and re-launch newly-ready phases. Repeats until all phases
    have completed or one fails.

    Phase order (used by the frontend to display curriculum-order rows) stays
    stable: it's the position in `content_phases` plus the head offset.
    """
    pending: set[str] = set(content_phases)
    in_flight: dict[str, asyncio.Task] = {}
    phase_order_map: dict[str, int] = {
        name: phase_order_offset + i for i, name in enumerate(content_phases)
    }

    def _ready(name: str) -> bool:
        deps = resolve_phase_deps(name, content_phases)
        return deps.issubset(prior_outputs.keys())

    failed = False

    while pending or in_flight:
        # Launch every phase whose deps are now satisfied. Multiple phases can
        # become ready in a single iteration (e.g., when an upstream completes
        # and unblocks two siblings).
        if not failed:
            ready_now = sorted(p for p in pending if _ready(p))
            for name in ready_now:
                pending.remove(name)
                in_flight[name] = asyncio.create_task(
                    _execute_one_phase(
                        job_id=job_id,
                        resource_id=resource_id,
                        log=log,
                        phase_name=name,
                        phase_order=phase_order_map[name],
                        total_phases_hint=phase_order_offset + len(content_phases),
                        subject=subject,
                        file_uri=file_uri,
                        file_phases=file_phases,
                        book_cache_name=book_cache_name,
                        job_text_cache_name=job_text_cache_name,
                        section_data=section_data,
                        lesson_context=lesson_context,
                        prior_outputs=prior_outputs,
                        difficulty=difficulty,
                    ),
                    name=f"phase:{name}",
                )

        if not in_flight:
            if pending and not failed:
                raise RuntimeError(
                    f"Phase scheduler stuck — pending={sorted(pending)} but no phase is ready. "
                    f"Resolved deps: {{p: list(resolve_phase_deps(p, content_phases)) for p in sorted(pending)}}"
                )
            break

        # Wait for the next phase to finish — first-completed semantics so we
        # can launch newly-unblocked successors as soon as possible.
        done, _ = await asyncio.wait(
            list(in_flight.values()), return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            phase_name = next(n for n, t in in_flight.items() if t is task)
            del in_flight[phase_name]
            try:
                output_md, _tin, _tout, parsed_struct = task.result()
            except Exception:
                # Already logged + marked failed by _execute_one_phase. Cancel
                # any peers still in flight and stop launching new phases.
                failed = True
                for peer in in_flight.values():
                    peer.cancel()
                # Drain cancellations so we don't leak tasks
                if in_flight:
                    await asyncio.gather(*in_flight.values(), return_exceptions=True)
                    in_flight.clear()
                continue

            prior_outputs[phase_name] = output_md
            if parsed_struct is not None and phase_name in _JSON_COLUMN_SETTERS:
                json_payload = parsed_struct.model_dump(mode="json")
                setter = _JSON_COLUMN_SETTERS[phase_name]
                async with SessionLocal() as session:
                    await setter(session, job_id, json_payload)
                    await session.commit()
                log.info(
                    f"[job {job_id}] {phase_name} JSON persisted | "
                    f"keys={list(json_payload.keys())}"
                )

    if failed:
        # Caller's surrounding try/except will see the original exception was
        # already published; raise a sentinel so it returns cleanly.
        raise RuntimeError("content phase failed")


async def _execute_phase(
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    subject: str,
    file_uri: str,
    attach_file: bool = False,
    cached_content: Optional[str] = None,
    cache_holds_text_context: bool = False,
    section: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
) -> tuple[str, Optional[int], Optional[int], str, Optional[Any]]:
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
                return cached_extract.output_md, 0, 0, prompt_hash, None

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
            parsed_struct: Optional[Any] = None
        elif phase_name in gemini.STRUCTURED_PHASE_SCHEMAS:
            # JSON-renderable phase: produce structured output in ONE Gemini
            # call instead of MD-then-extract (which paid for two roundtrips).
            phase_prompt = get_prompt(subject, phase_name)
            parsed_struct, tin, tout = await gemini.run_phase_prompt_structured(
                phase_prompt=phase_prompt,
                response_schema=gemini.STRUCTURED_PHASE_SCHEMAS[phase_name],
                file_uri=file_uri if attach_file else None,
                attach_file=attach_file,
                cached_content=cached_content,
                cache_holds_text_context=cache_holds_text_context,
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
                phase_name=phase_name,
                max_output_tokens=max_output_tokens_for(phase_name),
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
            output_md = _synth_md_for_structured(phase_name, parsed_struct)
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await gemini.run_phase_prompt(
                phase_prompt=phase_prompt,
                file_uri=file_uri if attach_file else None,
                attach_file=attach_file,
                cached_content=cached_content,
                cache_holds_text_context=cache_holds_text_context,
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
                phase_name=phase_name,
                max_output_tokens=max_output_tokens_for(phase_name),
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
            parsed_struct = None
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

    return output_md, tin, tout, prompt_hash, parsed_struct


def _parse_classify(output_md: str) -> str:
    """Parse the classify phase output. Returns "hard" or "easy".

    Defaults to "hard" on empty/ambiguous output — that's the conservative
    choice (more phases run, student gets the richer experience) and it
    surfaces classifier failures as visible "did this lesson really need the
    full HARD pipeline?" rather than silently downgrading to "easy".
    """
    text = (output_md or "").strip()
    if not text:
        logger.warning(
            "classify produced empty output — defaulting to HARD. "
            "Bump max_output_tokens for classify if this recurs."
        )
        return "hard"
    upper = text.upper()
    if "HARD" in upper:
        return "hard"
    if "EASY" in upper:
        return "easy"
    logger.warning(
        f"classify output contained neither 'HARD' nor 'EASY' "
        f"(text={text[:120]!r}) — defaulting to HARD"
    )
    return "hard"


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
