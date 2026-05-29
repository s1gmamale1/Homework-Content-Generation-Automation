from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
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
from app.services import agent, events_bus
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

    if phase_name == "case-based-preview":
        title = getattr(parsed, "title", None) or "Case-Based Preview"
        role = getattr(parsed, "student_role", "") or ""
        cps = getattr(parsed, "checkpoints", None) or []
        dpe = getattr(parsed, "decision_process_explanation", None)
        sim = getattr(parsed, "final_simulation", None)
        out = [
            f"## {title}",
            f"_Student role: {role}. {len(cps)} checkpoints + DPE slot 7. "
            f"Interactive CBP rendered in preview._\n",
        ]
        for i, cp in enumerate(cps, 1):
            out.append(f"**Checkpoint {i}** [{cp.intent}] {cp.question}")
        if dpe:
            out.append(f"\n**Decision Process Explanation (slot 7):** {dpe.prompt}")
        if sim:
            out.append(f"\n**Correct path:** {sim.correct_path}")
            out.append(f"**Wrong path:** {sim.wrong_path}")
        return "\n".join(out)

    if phase_name == "flashcards":
        cards = getattr(parsed, "cards", None) or []
        out = [f"_{len(cards)} flashcards — interactive deck rendered in preview._\n"]
        for i, c in enumerate(cards, 1):
            card_id = getattr(c, "id", "") or ""
            id_tag = f" `{card_id}`" if card_id else ""
            out.append(f"{i}.{id_tag} **{c.front}** — {c.back}")
            if getattr(c, "hint", None):
                out.append(f"   - hint: {c.hint}")
        return "\n".join(out)

    if phase_name == "memory-check":
        items = getattr(parsed, "items", None) or []
        threshold = getattr(parsed, "pass_threshold", 0.60)
        out = [
            f"_{len(items)} memory-check items (pass ≥{int(threshold * 100)}%) "
            f"— interactive check rendered in preview._\n"
        ]
        for i, it in enumerate(items, 1):
            fid = getattr(it, "flashcard_id", "") or ""
            fid_tag = f" [←{fid}]" if fid else ""
            out.append(f"{i}. **[{it.kind.upper()}]{fid_tag}** {it.prompt}")
            for j, opt in enumerate(it.options or []):
                marker = "✓" if it.correct_index is not None and j == it.correct_index else " "
                out.append(f"   - [{marker}] {opt}")
            if getattr(it, "explanation", None):
                out.append(f"   - _{it.explanation}_")
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

    if phase_name == "boss-arena":
        qs = getattr(parsed, "questions", None) or []
        title = getattr(parsed, "title", None) or "Boss Arena"
        hp = getattr(parsed, "starting_hp", 100)
        out = [
            f"_{title} — Why→How→What reasoning boss with {len(qs)} questions, "
            f"starting HP {hp}._\n"
        ]
        for i, q in enumerate(qs, 1):
            concepts = ", ".join(getattr(q, "concept_ids", None) or [])
            out.append(
                f"{i}. **[{q.difficulty.upper()} · -{q.base_damage} HP]** _{q.scenario}_"
            )
            out.append(f"   - **Why:** {q.why}")
            out.append(f"   - **How:** {q.how}")
            out.append(f"   - **What:** {q.what}")
            if concepts:
                out.append(f"   - _concepts: {concepts}_")
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

    if phase_name == "practice-rlc":
        decisions = getattr(parsed, "decisions", None) or []
        concepts = ", ".join(getattr(parsed, "concept_ids", None) or [])
        out = [
            f"## Real-Life Challenge — _{parsed.role}_",
            f"**Task:** {parsed.task}\n",
            f"{parsed.context}\n",
            f"**Prediction:** {parsed.prediction_prompt}\n",
        ]
        for i, d in enumerate(decisions, 1):
            out.append(f"**Decision {i}:** {d.question}")
            for j, opt in enumerate(d.options):
                marker = "✓" if j == d.correct_option else " "
                out.append(f"   - [{marker}] {opt}")
        out.append(f"\n**Final summary:** {parsed.final_summary}")
        if concepts:
            out.append(f"_concepts: {concepts}_")
        return "\n".join(out)

    if phase_name == "practice-error-detection":
        blocks = getattr(parsed, "blocks", None) or []
        concepts = ", ".join(getattr(parsed, "concept_ids", None) or [])
        out = [
            f"## Error Detection _({parsed.pattern})_",
            "_Find the one broken block, then type the correction._\n",
        ]
        for b in blocks:
            tag = " ← broken" if b.is_error else ""
            out.append(f"- `{b.content}`{tag}")
        out.append(f"\n**Correct version:** {parsed.correct_answer_for_error_block}")
        if parsed.hint:
            out.append(f"**Hint:** {parsed.hint}")
        if parsed.why_prompt:
            out.append(f"**Why:** {parsed.why_prompt}")
        if concepts:
            out.append(f"_concepts: {concepts}_")
        return "\n".join(out)

    if phase_name in _CBP_MODE_PHASES:
        mode = getattr(parsed, "interaction_mode", "") or ""
        title = getattr(parsed, "title", None) or "Practice Game"
        role = getattr(parsed, "student_role", "") or ""
        cps = getattr(parsed, "checkpoints", None) or []
        dpe = getattr(parsed, "decision_process_explanation", None)
        sim = getattr(parsed, "final_simulation", None)
        out = [
            f"## {title} _(CBP mode: {mode})_",
            f"_Student role: {role}. {len(cps)} checkpoints + DPE._\n",
        ]
        for i, cp in enumerate(cps, 1):
            out.append(f"**Checkpoint {i}** [{cp.intent}] {cp.question}")
        if dpe:
            out.append(f"\n**Decision Process Explanation:** {dpe.prompt}")
        if sim:
            out.append(f"\n**Correct path:** {sim.correct_path}")
            out.append(f"**Wrong path:** {sim.wrong_path}")
        return "\n".join(out)

    return ""


_JSON_COLUMN_SETTERS = {
    "flashcards": jobs_repo.set_flashcards_json,
    "memory-sprint": jobs_repo.set_memory_sprint_json,
    "game-breaks": jobs_repo.set_games_json,
    "final-challenge": jobs_repo.set_final_challenge_json,
    "boss-arena": jobs_repo.set_boss_arena_json,
    "case-based-preview": jobs_repo.set_cbp_json,
    "memory-check": jobs_repo.set_memory_check_json,
    "reading": jobs_repo.set_reading_json,
    # PR-3 Practice Arc games.
    "practice-rlc": jobs_repo.set_practice_rlc_json,
    "practice-error-detection": jobs_repo.set_practice_error_detection_json,
    "practice-memory-match": jobs_repo.set_practice_memory_match_json,
    "practice-tictactoe": jobs_repo.set_practice_tictactoe_json,
    "practice-jigsaw": jobs_repo.set_practice_jigsaw_json,
    "practice-sentence": jobs_repo.set_practice_sentence_json,
}

# The four CBP-mode game phases share the CaseBasedPreview-derived synth render.
_CBP_MODE_PHASES = {
    "practice-memory-match",
    "practice-tictactoe",
    "practice-jigsaw",
    "practice-sentence",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run(job_id: UUID) -> None:
    """Execute a homework job: extract → classify? → content phases → assemble."""
    resource_id = f"job:{job_id}"
    log = logger.bind(job_id=str(job_id))
    t_start = perf_counter()

    log.info(f"[job {job_id}] pipeline starting")

    try:
        # ─── load job + book + section ─────────────────────────
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
                log.warning(f"[job {job_id}] not found, aborting")
                return
            book = await books_repo.get(session, job.book_id)
            section = await toc_repo.get(session, job.toc_entry_id)
            if book is None or section is None:
                raise RuntimeError("Job is missing book or section context")
            subject = book.subject
            book_id = book.id
            # Per-job provider/model. Pinned at job-creation time so retries
            # hit the same backend; ``model`` may be None — agent._resolve_model
            # falls back to either a hardcoded provider default or the CLI's
            # own default in that case.
            provider = job.provider
            model = job.model
            section_data = {
                "id": section.id,
                "title": section.section_title,
                "number": section.section_number,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "chapter": section.chapter_title or "",
            }

        # Local on-disk PDF — written by app.api.v1.books.upload_book and kept
        # for the lifetime of the book (no Files-API URI anymore).
        pdf_path = Path("var") / "books" / str(book_id) / "source.pdf"
        if not pdf_path.exists():
            raise RuntimeError(f"Book PDF missing on disk: {pdf_path}")

        log.info(
            f"[job {job_id}] context loaded | subject={subject} "
            f"provider={provider} model={model or '<default>'} "
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
        # PR-1/plan §10: the source map digest threaded into every content
        # phase prompt as the authoritative concept list (source fidelity).
        source_map_digest: str = ""
        phase_order = 0

        file_phases = file_needed_phases(subject)
        log.info(
            f"[job {job_id}] file-needed phases for '{subject}': "
            f"{sorted(file_phases) or '(none beyond extract)'}"
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
                    provider=provider,
                    model=model,
                    pdf_path=pdf_path,
                    file_phases=file_phases,
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
                # PR-1: derive the structured source map from the freshly
                # captured lesson_context — text-only, pinned to the cheap
                # extractor (no PDF re-read). Best-effort: a failure logs but
                # does NOT fail the job (downstream phases don't consume the
                # map yet; that wiring lands in later PRs).
                try:
                    source_map = await agent.extract_source_map(
                        provider=settings.extract_provider,
                        model=settings.extract_model,
                        lesson_context=lesson_context,
                        subject_family=subject,
                        chapter=section_data.get("chapter") or "",
                        section=section_data["title"],
                        homework_job_id=job_id,
                    )
                    source_map_payload = source_map.model_dump(mode="json")
                    async with SessionLocal() as session:
                        await jobs_repo.set_source_map_json(
                            session, job_id, source_map_payload
                        )
                        await session.commit()
                    # Thread the map into downstream content phases (plan §10).
                    source_map_digest = agent.format_source_map_digest(
                        source_map_payload
                    )
                    log.info(
                        f"[job {job_id}] source map captured | "
                        f"concepts={len(source_map.concepts)}"
                    )
                    await events_bus.publish(
                        resource_id,
                        "source_map_ready",
                        {"concepts": len(source_map.concepts)},
                    )
                except Exception as exc:
                    log.warning(
                        f"[job {job_id}] source map extraction failed "
                        f"(non-fatal): {exc!r}"
                    )
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
                    provider=provider,
                    model=model,
                    pdf_path=pdf_path,
                    file_phases=file_phases,
                    section_data=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                    source_map_digest=source_map_digest,
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
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    file_phases: set[str],
    section_data: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    source_map_digest: str = "",
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

    try:
        output_md, tin, tout, _ph, parsed_struct = await _execute_phase(
            job_id=job_id,
            phase_name=phase_name,
            phase_order=phase_order,
            subject=subject,
            provider=provider,
            model=model,
            pdf_path=pdf_path,
            attach_file=phase_needs_file,
            section=section_data,
            lesson_context=lesson_context,
            prior_outputs=phase_prior,
            difficulty=difficulty,
            source_map_digest=source_map_digest,
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
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    file_phases: set[str],
    section_data: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    source_map_digest: str = "",
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
                        provider=provider,
                        model=model,
                        pdf_path=pdf_path,
                        file_phases=file_phases,
                        section_data=section_data,
                        lesson_context=lesson_context,
                        prior_outputs=prior_outputs,
                        difficulty=difficulty,
                        source_map_digest=source_map_digest,
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
    provider: str,
    model: Optional[str],
    pdf_path: Path,
    attach_file: bool = False,
    section: dict,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    difficulty: Optional[str],
    source_map_digest: str = "",
) -> tuple[str, Optional[int], Optional[int], str, Optional[Any]]:
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v1"
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)

    # Per-phase model_name on the phase row records exactly what served this
    # call. The ``extract`` phase is pinned to the cheap-extractor settings
    # regardless of the job-level provider/model; every other phase honors
    # the user's pick.
    if phase_name == "extract":
        phase_model_label = settings.extract_model
    else:
        phase_model_label = model or "<provider-default>"

    async with SessionLocal() as session:
        # ``create_or_reset`` (not ``create``) so retries of a job whose phase
        # row already exists from a previous, killed run don't crash on the
        # ``uq_phase_output_job_order`` unique constraint. The orphan sweep in
        # ``main.lifespan`` only marks stale phase rows as ``failed``; it does
        # not delete them.
        po = await phase_repo.create_or_reset(
            session,
            job_id=job_id,
            phase_name=phase_name,
            phase_order=phase_order,
            prompt_hash=prompt_hash,
            model_name=phase_model_label,
        )
        await phase_repo.set_status(session, po.id, "running", started_at=_utcnow())
        await jobs_repo.set_status(session, job_id, "running", current_phase=phase_name)
        await session.commit()
        po_id = po.id

    logger.debug(
        f"[job {job_id}] phase row created | phase={phase_name} order={phase_order} "
        f"prompt_hash={prompt_hash[:12]} provider={provider} model={phase_model_label}"
    )

    try:
        if phase_name == "extract":
            # Cross-job cache: if we've already extracted this section under
            # the current builtin extract prompt, reuse the prior output and
            # skip the agent call entirely. Saves ~15s + ~1.5K output tokens
            # per regeneration / repeat job on the same section.
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
                    f"po={cached_extract.id} (skipping agent call)"
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
                # Visibility: record a free agent_usages row
                await agent.record_cached_lesson_extract(
                    homework_job_id=job_id,
                    phase_output_id=po_id,
                    source_job_id=cached_extract.job_id,
                    source_phase_output_id=cached_extract.id,
                )
                return cached_extract.output_md, 0, 0, prompt_hash, None

            # Pin lesson.extract to the cheap-extractor model regardless of
            # the job's per-phase provider/model: it's a high-input/low-value
            # factual summary, paying smart-tier rates here saves nothing.
            output_md, tin, tout = await agent.extract_lesson_context(
                provider=settings.extract_provider,
                model=settings.extract_model,
                pdf_path=pdf_path,
                section_title=section["title"],
                section_number=section["number"],
                page_start=section["page_start"],
                page_end=section["page_end"],
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
            parsed_struct: Optional[Any] = None
        elif phase_name in agent.STRUCTURED_PHASE_SCHEMAS:
            # JSON-renderable phase: produce structured output in ONE call
            # instead of MD-then-extract (which paid for two roundtrips).
            phase_prompt = get_prompt(subject, phase_name)
            parsed_struct, tin, tout = await agent.run_phase_prompt_structured(
                provider=provider,
                model=model,
                phase_prompt=phase_prompt,
                response_schema=agent.STRUCTURED_PHASE_SCHEMAS[phase_name],
                attachments=[pdf_path] if attach_file else [],
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
                phase_name=phase_name,
                max_output_tokens=max_output_tokens_for(phase_name),
                homework_job_id=job_id,
                phase_output_id=po_id,
                source_map_digest=source_map_digest,
            )
            output_md = _synth_md_for_structured(phase_name, parsed_struct)
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await agent.run_phase_prompt(
                provider=provider,
                model=model,
                phase_prompt=phase_prompt,
                attachments=[pdf_path] if attach_file else [],
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
                phase_name=phase_name,
                max_output_tokens=max_output_tokens_for(phase_name),
                homework_job_id=job_id,
                phase_output_id=po_id,
                source_map_digest=source_map_digest,
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


# PR-5 assembly reshape (plan §8). Ordered phase → display-name within each of
# the three Flow v2 divisions. Legacy phase names are kept so older jobs still
# render under the right division. A subject only ran a subset of these.
_LEARNING_PHASES: list[tuple[str, str]] = [
    ("case-based-preview", "Case-Based Preview"),
    ("reading", "Reading"),
    ("flashcards", "Flashcard Learning"),
    ("memory-check", "Memory Check"),
    # legacy
    ("memory-sprint", "Memory Sprint"),
    ("preview-hard", "Preview"),
    ("preview-easy", "Preview"),
    ("preview", "Preview"),
]
_PRACTICE_PHASES: list[tuple[str, str]] = [
    ("practice-rlc", "Real-Life Challenge"),
    ("practice-error-detection", "Error Detection"),
    ("practice-memory-match", "Memory Matching"),
    ("practice-tictactoe", "TicTacToe"),
    ("practice-jigsaw", "Jigsaw Matching"),
    ("practice-sentence", "Sentence Filling"),
    # legacy
    ("game-breaks", "Game Breaks"),
    ("real-life", "Real-Life Challenge"),
    ("consolidation", "Consolidation"),
]
_BOSS_PHASES: list[tuple[str, str]] = [
    ("boss-arena", "Boss Arena"),
    ("final-challenge", "Final Challenge"),  # legacy
]
_REFLECT_PHASES: list[tuple[str, str]] = [("reflection", "Reflection")]


def _strip_leading_md_heading(body: str) -> str:
    """Drop a leading markdown heading line (``#``/``##``/``###`` …) from a
    phase body so it doesn't double up with the ``###`` subsection heading the
    assembler adds. Leaves non-heading bodies untouched."""
    if not body:
        return body
    stripped = body.lstrip("\n")
    lines = stripped.split("\n", 1)
    if lines and lines[0].lstrip().startswith("#"):
        rest = lines[1] if len(lines) > 1 else ""
        return rest.lstrip("\n")
    return body


def _render_source_map_md(source_map_json: Optional[dict]) -> str:
    if not source_map_json:
        return ""
    concepts = source_map_json.get("concepts") or []
    if not concepts:
        return ""
    lines = []
    for c in concepts:
        cid = c.get("id", "")
        label = c.get("label", "")
        statement = c.get("statement", "")
        kind = c.get("kind")
        kind_tag = f" _({kind})_" if kind else ""
        lines.append(f"- **[{cid}]** {label} — {statement}{kind_tag}")
    return "\n".join(lines)


def _render_division(
    title: str,
    ordered: list[tuple[str, str]],
    phase_bodies: dict[str, str],
    rendered: set[str],
) -> str:
    """Render one division (e.g. Learning Sections) as a ``## title`` block with
    each present phase as a ``### display name`` subsection. Returns "" when no
    phase in the division ran. Marks rendered phases in ``rendered``."""
    blocks: list[str] = []
    for phase_name, display in ordered:
        if phase_name in phase_bodies and phase_name not in rendered:
            rendered.add(phase_name)
            body = _strip_leading_md_heading(phase_bodies[phase_name] or "").strip()
            blocks.append(f"### {display}\n\n{body or '(empty)'}")
    if not blocks:
        return ""
    return f"## {title}\n\n" + "\n\n".join(blocks)


def _render_homework_md(
    *,
    book_title: Optional[str],
    chapter: Optional[str],
    section_number: Optional[str],
    section_title: Optional[str],
    page_start: Optional[int],
    page_end: Optional[int],
    extract_md: Optional[str],
    source_map_json: Optional[dict],
    phase_bodies: dict[str, str],
) -> str:
    """Pure Flow v2 markdown assembler (plan §8). Takes already-loaded data and
    returns the human-handoff packet. Kept pure so it's unit-testable without a
    DB."""
    out: list[str] = ["# Homework Content", ""]

    # Source book / chapter / section.
    out.append("## Source Book / Chapter / Section")
    meta_lines = []
    if book_title:
        meta_lines.append(f"- **Book:** {book_title}")
    if chapter:
        meta_lines.append(f"- **Chapter:** {chapter}")
    sec = " ".join(s for s in [section_number, section_title] if s)
    if sec:
        meta_lines.append(f"- **Section:** {sec}")
    if page_start is not None and page_end is not None:
        meta_lines.append(f"- **Pages:** {page_start}–{page_end}")
    out.append("\n".join(meta_lines) if meta_lines else "_(metadata unavailable)_")

    # Extracted section summary (the pinned extract output).
    if extract_md and extract_md.strip():
        out.append("\n## Extracted Section Summary")
        out.append(extract_md.strip())

    # Source map (previously persisted to JSON only — now in the handoff).
    sm = _render_source_map_md(source_map_json)
    if sm:
        out.append("\n## Source Map")
        out.append(sm)

    rendered: set[str] = set()
    for title, ordered in (
        ("Learning Sections", _LEARNING_PHASES),
        ("Practice Arc", _PRACTICE_PHASES),
        ("Boss Arena", _BOSS_PHASES),
        ("Reflection", _REFLECT_PHASES),
    ):
        block = _render_division(title, ordered, phase_bodies, rendered)
        if block:
            out.append("\n" + block)

    # Safety net: never silently drop a phase the divisions didn't cover.
    leftovers = [
        name for name in phase_bodies
        if name not in rendered and name not in _INTERNAL_PHASES
    ]
    if leftovers:
        extra_blocks = []
        for name in leftovers:
            display = name.replace("-", " ").title()
            body = _strip_leading_md_heading(phase_bodies[name] or "").strip()
            extra_blocks.append(f"### {display}\n\n{body or '(empty)'}")
        out.append("\n## Other\n\n" + "\n\n".join(extra_blocks))

    return "\n".join(out).strip() + "\n"


async def _assemble(job_id: UUID) -> str:
    async with SessionLocal() as session:
        job = await jobs_repo.get(session, job_id)
        phases = await phase_repo.list_for_job(session, job_id)
        book = await books_repo.get(session, job.book_id) if job else None
        section = await toc_repo.get(session, job.toc_entry_id) if job else None

    phase_bodies = {p.phase_name: (p.output_md or "") for p in phases}
    extract_md = phase_bodies.get("extract")
    source_map_json = job.source_map_json if job else None

    return _render_homework_md(
        book_title=getattr(book, "title", None),
        chapter=getattr(section, "chapter_title", None),
        section_number=getattr(section, "section_number", None),
        section_title=getattr(section, "section_title", None),
        page_start=getattr(section, "page_start", None),
        page_end=getattr(section, "page_end", None),
        extract_md=extract_md,
        source_map_json=source_map_json,
        phase_bodies=phase_bodies,
    )


async def _log_token_summary(job_id: UUID, log) -> None:
    """End-of-pipeline summary: per-call token cost as a flat ASCII table.

    Renders one row per ``agent_usages`` row for this job (plus a TOTAL footer)
    so the optimizations are immediately verifiable from the terminal — small
    `fresh` columns alongside non-zero `cached` columns means the provider's
    own implicit prompt cache is hitting.

    Reads token counts from the provider-neutral columns
    (``prompt_tokens``, ``output_tokens``, ``cached_tokens``). Modality
    breakdowns no longer exist in the new schema, so we drop the IMAGE/PDF
    column — providers that report attachments inline aren't comparable
    anyway.
    """
    from sqlalchemy import select  # local import: only used here

    from app.models import AgentUsage

    async with SessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(AgentUsage)
                    .where(AgentUsage.homework_job_id == job_id)
                    .order_by(AgentUsage.created_at)
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        return

    OP_W = 28
    PROV_W = 9
    header = (
        f"{'operation':<{OP_W}}"
        f"{'provider':<{PROV_W}}"
        f"{'prompt':>10}{'cached':>10}{'fresh':>10}{'out':>9}{'dur':>9}  ok"
    )
    bar = "─" * len(header)
    lines = [bar, header, bar]

    total_in = total_out = total_cached = 0
    for r in rows:
        prompt_in = int(r.prompt_tokens or 0)
        cached = int(r.cached_tokens or 0)
        out_tokens = int(r.output_tokens or 0)
        fresh_in = max(prompt_in - cached, 0)

        ok = "✓" if r.success else "✗"
        # Decorate operation with the phase name when available — pulled from
        # the raw envelope where _record_usage stashed it.
        op_label = r.operation
        envelope = r.raw_envelope or {}
        phase_name = envelope.get("phase_name")
        if isinstance(phase_name, str):
            op_label = f"{r.operation}:{phase_name}"
        if len(op_label) > OP_W - 1:
            op_label = op_label[: OP_W - 2] + "…"

        prov_label = (r.provider or "?")[: PROV_W - 1]

        lines.append(
            f"{op_label:<{OP_W}}"
            f"{prov_label:<{PROV_W}}"
            f"{prompt_in:>10,}"
            f"{cached:>10,}"
            f"{fresh_in:>10,}"
            f"{out_tokens:>9,}"
            f"{(r.duration or '—'):>9}"
            f"  {ok}"
        )
        total_in += prompt_in
        total_out += out_tokens
        total_cached += cached

    fresh_total = max(total_in - total_cached, 0)
    cache_pct = (total_cached / total_in * 100) if total_in else 0

    lines.append(bar)
    lines.append(
        f"{'TOTAL':<{OP_W}}"
        f"{'':<{PROV_W}}"
        f"{total_in:>10,}"
        f"{total_cached:>10,}"
        f"{fresh_total:>10,}"
        f"{total_out:>9,}"
        f"{'':>9}"
    )
    lines.append(
        f"  {len(rows)} calls · "
        f"cache hit: {cache_pct:.0f}% · "
        f"net billed input (fresh): {fresh_total:,}"
    )
    lines.append(bar)

    log.info(f"[job {job_id}] token summary\n" + "\n".join(lines))
