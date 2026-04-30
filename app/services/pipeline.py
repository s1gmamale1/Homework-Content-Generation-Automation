from __future__ import annotations

from datetime import datetime, timezone
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
from app.services.flows import SUBJECT_FLOWS
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
            section_data = {
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

        # ─── phase loop ────────────────────────────────────────
        while phase_order < len(sequence):
            phase_name = sequence[phase_order]
            log.info(
                f"[job {job_id}] phase {phase_order + 1}/{len(sequence)} "
                f"'{phase_name}' starting"
            )
            t_phase = perf_counter()
            await _emit_started(resource_id, phase_name, phase_order)

            try:
                output_md, tin, tout, _ph = await _execute_phase(
                    job_id=job_id,
                    phase_name=phase_name,
                    phase_order=phase_order,
                    subject=subject,
                    file_uri=file_uri,
                    section=section_data,
                    lesson_context=lesson_context,
                    prior_outputs=prior_outputs,
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
        total_tokens_in = 0  # tracked for summary only
        total_tokens_out = 0
        log.success(
            f"[job {job_id}] pipeline complete | phases_run={len(sequence)} "
            f"assembled_chars={len(assembled)} total_s={total_s:.1f}"
        )
        # silence unused-warning (kept for future cumulative metric)
        _ = total_tokens_in, total_tokens_out

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


async def _execute_phase(
    *,
    job_id: UUID,
    phase_name: str,
    phase_order: int,
    subject: str,
    file_uri: str,
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
            output_md, tin, tout = await gemini.extract_lesson_context(
                file_uri=file_uri,
                section_title=section["title"],
                section_number=section["number"],
                page_start=section["page_start"],
                page_end=section["page_end"],
                homework_job_id=job_id,
                phase_output_id=po_id,
            )
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await gemini.run_phase_prompt(
                phase_prompt=phase_prompt,
                file_uri=file_uri,
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
