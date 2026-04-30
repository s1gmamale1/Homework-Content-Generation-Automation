import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.config import settings
from app.db import SessionLocal
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import toc_entries as toc_repo
from app.services import events_bus, gemini
from app.services.flows import SUBJECT_FLOWS
from app.services.prompts import get_prompt, get_prompt_hash

log = logging.getLogger(__name__)

_INTERNAL_PHASES = {"extract", "classify"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run(job_id: UUID) -> None:
    resource_id = f"job:{job_id}"
    try:
        async with SessionLocal() as session:
            job = await jobs_repo.get(session, job_id)
            if job is None:
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

        flow = SUBJECT_FLOWS[subject]
        sequence: list[str] = ["extract"]
        if flow["has_classify"]:
            sequence.append("classify")
            # content phases get appended after classify resolves difficulty
        else:
            # no classify → subject runs its hard sequence by default
            # (e.g., history's easy list is empty by design)
            sequence.extend(flow["hard"])

        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "running", started_at=_utcnow())
            await session.commit()

        difficulty: Optional[str] = None
        prior_outputs: dict[str, str] = {}
        lesson_context: Optional[str] = None
        phase_order = 0

        while phase_order < len(sequence):
            phase_name = sequence[phase_order]
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
                log.exception("Phase %s failed for job %s", phase_name, job_id)
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

            if phase_name == "extract":
                lesson_context = output_md
            elif phase_name == "classify":
                difficulty = _parse_classify(output_md)
                async with SessionLocal() as session:
                    await jobs_repo.set_difficulty(session, job_id, difficulty)
                    await session.commit()
                await events_bus.publish(
                    resource_id, "difficulty_classified", {"difficulty": difficulty}
                )
                sequence.extend(flow[difficulty])
            else:
                prior_outputs[phase_name] = output_md

            phase_order += 1

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
    except Exception as exc:
        log.exception("Pipeline crashed for job %s", job_id)
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
        await phase_repo.set_status(
            session, po.id, "running", started_at=_utcnow()
        )
        await jobs_repo.set_status(session, job_id, "running", current_phase=phase_name)
        await session.commit()
        po_id = po.id

    try:
        if phase_name == "extract":
            output_md, tin, tout = await gemini.extract_lesson_context(
                file_uri=file_uri,
                section_title=section["title"],
                section_number=section["number"],
                page_start=section["page_start"],
                page_end=section["page_end"],
            )
        else:
            phase_prompt = get_prompt(subject, phase_name)
            output_md, tin, tout = await gemini.run_phase_prompt(
                phase_prompt=phase_prompt,
                file_uri=file_uri,
                lesson_context=lesson_context or "",
                prior_outputs=prior_outputs,
                difficulty=difficulty,
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
