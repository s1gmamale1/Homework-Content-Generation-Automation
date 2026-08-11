from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, NoReturn, Optional
from uuid import UUID

from loguru import logger
from pydantic import ValidationError

from app.config import settings
from app.db import SessionLocal
from app.schemas.content_json import SCHEMAS, TeacherDeck
from app.repositories import books as books_repo
from app.repositories import jobs as jobs_repo
from app.repositories import phase_outputs as phase_repo
from app.repositories import toc_entries as toc_repo
from app.services import agent, book_fetch, content_lint, events_bus, failure_classifier, model_tiers, notion_archive, phase_judge, solver, storage, subjects
from app.services.agent_models import resolve_role_transport, resolve_session_limit_strategy
from app.services.errors import (
    CancelWonSignal,
    LeaseLostSignal,
    PhaseAttemptTimeout,
    PersistentSolverMismatch,
    SessionLimitPause,
    SlotSaturation,
    TransientPhaseError,
    is_slot_saturation,
)
from app.services.lease import CancelRequested, JobLease, LeaseLost
from app.services.flows import (
    flow_for,
    teacher_material_flow_for,
    file_needed_phases,
    filter_prior_outputs,
    max_output_tokens_for,
    resolve_phase_deps,
)
from app.services.phase_artifact import (
    PhaseArtifact,
    StructuredPhaseError,
    artifact_from_config,
    artifact_from_markdown,
)
from app.services.prompts import get_prompt, get_prompt_hash, get_structured_prompt

_INTERNAL_PHASES = {"extract", "classify"}

# CQ-C: key-bearing phases the independent answer-key solver re-checks after
# the judge has run (so it checks the FINAL, possibly judge-regenerated output).
_SOLVER_PHASES = ("memory-check", "practice-error-detection", "practice-rlc", "boss-arena")


def _inject_grade(lesson_context: Optional[str], grade: Optional[str]) -> Optional[str]:
    """Prepend the student grade to the lesson context so the content-phase prompts'
    grade-band rules (deck size, reasoning load, distractor subtlety, question count)
    have a value to read — without this the grade never reached content generation.
    No-op when grade or lesson_context is missing."""
    if not grade or lesson_context is None:
        return lesson_context
    return f"Student grade level: {grade}\n\n{lesson_context}"


def _inject_lesson_boundary(
    lesson_context: Optional[str], next_lesson_title: Optional[str]
) -> Optional[str]:
    """Prepend a curriculum-boundary note naming the NEXT lesson so content
    phases stop at this lesson's edge instead of reaching for the concept's
    natural completion (the audit's #1 defect: Pythagorean converse, parallelogram
    criteria, 'asymptote' — all next-lesson material). Rides inside lesson_context
    so every content phase sees it via _build_master_prompt's LESSON CONTEXT block;
    extract is unaffected (its lesson_context is None). No-op when there is no
    successor (last lesson) or no context."""
    if not next_lesson_title or lesson_context is None:
        return lesson_context
    note = (
        "CURRICULUM BOUNDARY:\n"
        f"The NEXT lesson in this textbook is: «{next_lesson_title}».\n"
        "Teach and test ONLY the CURRENT lesson's concepts. Do NOT use, teach, "
        "hint at, or build any question on the next lesson's material — including "
        "the converse or inverse of this lesson's theorem/rule, its recognition "
        "criteria (alomatlari), or any generalization the next lesson introduces. "
        "If a natural 'next step' of this concept belongs to the next lesson, stop "
        "at this lesson's boundary."
    )
    return f"{note}\n\n{lesson_context}"


def _scheduler_stuck_message(pending, content_phases: list[str]) -> str:
    """Build the diagnostic message for a stuck DAG scheduler.

    Pure helper (no I/O, no DB) so it is unit-testable in isolation.
    Computes the resolved-deps dict as a real Python value instead of
    leaving the comprehension as a literal f-string (the original bug).
    """
    resolved = {
        p: sorted(resolve_phase_deps(p, content_phases)) for p in sorted(pending)
    }
    return (
        f"Phase scheduler stuck — pending={sorted(pending)} but no phase is ready. "
        f"Resolved deps: {resolved}"
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_of(lease: Optional[JobLease]):
    """The claim token to fence a worker-owned write with, or None (legacy /
    unfenced) when no lease is threaded — a fenced write with claim_token=None
    keeps the exact pre-fencing behavior."""
    return lease.claim_token if lease is not None else None


def _raise_on_lease_signal(result) -> None:
    """Convert a fenced-write sentinel into the matching CONTROL SIGNAL.

    A fenced ``jobs_repo`` / ``phase_repo`` write returns the job/phase id (or a
    plain bool) on success, or a ``lease.LeaseLost`` / ``lease.CancelRequested``
    sentinel when the lease no longer owns the row. Those sentinels must NOT be
    treated as content errors — they are raised here as ``LeaseLostSignal`` /
    ``CancelWonSignal`` and re-raised through every broad ``except`` boundary
    until ``worker._execute_job`` acts on them. A None/bool/id result is a no-op
    (so this is safe to call after every fenced write, lease or not)."""
    if result is LeaseLost:
        raise LeaseLostSignal()
    if result is CancelRequested:
        raise CancelWonSignal()


async def _persist_solver_blocked_phase(
    *,
    po_id: UUID,
    artifact: PhaseArtifact,
    tin: Optional[int],
    tout: Optional[int],
    produced_by: str,
    warnings: list[str],
    judge_status: Optional[str],
    error: PersistentSolverMismatch,
    claim_token: Optional[UUID],
) -> None:
    """Fence and commit the final inspected artifact as a quality failure.

    The caller raises ``error`` only after this write commits. A lost lease or
    completed cancellation wins over the content verdict and is surfaced as
    the existing control signal instead.
    """
    async with SessionLocal() as session:
        result = await phase_repo.set_status(
            session,
            po_id,
            "failed",
            completed_at=_utcnow(),
            output_md=artifact.output_md,
            tokens_input=tin,
            tokens_output=tout,
            error_message=str(error),
            validation_warnings=warnings or None,
            provider=produced_by,
            judge_status=judge_status,
            solver_status="mismatch_blocked",
            content_json=artifact.content_json,
            authoring_mode=artifact.authoring_mode,
            content_schema_version=artifact.content_schema_version,
            renderer_version=artifact.renderer_version,
            claim_token=claim_token,
        )
        await session.commit()
    _raise_on_lease_signal(result)


def _done_phase_md(rows) -> dict[str, str]:
    """Phase rows that are `done` with non-empty markdown — the resumable set."""
    return {
        r.phase_name: r.output_md
        for r in rows
        if r.status == "done" and (r.output_md or "").strip()
    }


def _coverage_warnings_for_job(rows) -> "list[str]":
    """Given phase rows (dicts or ORM objs with phase_name/output_md), compare the
    extract contract against the assembled packet and return warn-only coverage
    findings (lint:coverage_thin, may be empty). Pure; safe to call anywhere."""
    def _f(r, k):
        return r.get(k) if isinstance(r, dict) else getattr(r, k, None)
    extract = next((_f(r, "output_md") for r in rows if _f(r, "phase_name") == "extract"), None)
    if not extract:
        return []
    packet = "\n\n".join(
        _f(r, "output_md") or "" for r in rows if _f(r, "phase_name") != "extract")
    return content_lint.findings_to_warnings(content_lint.lint_coverage(extract, packet))


def _resolve_extract(job_extract_provider, job_extract_model, ld):
    """Extract role provider/model: explicit job override, else the global
    default from the launch_defaults DB row (jobs are stamped at launch; this
    is the defensive null-path, no settings read)."""
    return (
        job_extract_provider or ld.extract_provider,
        job_extract_model or ld.extract_model,
    )


def _plan_full_flow(kind: str, subject: str) -> list[str]:
    """Pure helper: pick the full content-phase flow for a job's `kind`.
    Factored out of `run()`'s sequence-planning block so it's unit-testable
    without a DB/pipeline run. `kind` must be a local captured ONCE from the
    ORM `job` object earlier in `run()` (mirrors `provider`/`model`) — reading
    `job.kind` this late risks `DetachedInstanceError` if the session has
    since closed."""
    if kind == "teacher_material":
        return teacher_material_flow_for(subject)
    return flow_for(subject)


def _pending_phases(content_phases: list[str], prior_outputs: dict[str, str]) -> set[str]:
    """Content phases still to run: everything not already in prior_outputs
    (done phases get pre-injected, so they're excluded and serve as deps)."""
    return {p for p in content_phases if p not in prior_outputs}


async def run(job_id: UUID, lease: Optional[JobLease] = None) -> None:
    """Execute a homework job: extract → content phases → assemble.

    ``lease`` (fenced job leases, Task 7): the per-execution ``JobLease`` the
    worker minted at claim time. Every worker-owned write (the ``running`` /
    ``done`` / ``failed`` job-status writes and every phase write) is fenced
    with ``lease.claim_token`` so a reclaimed-then-resumed obsolete worker can
    never mutate a job that now belongs to another worker. A fenced write that
    finds the lease gone raises ``LeaseLostSignal``; one that finds a user
    cancel already finalized raises ``CancelWonSignal`` — both unwind cleanly
    (never a content error / queue retry) up to ``worker._execute_job``.
    ``lease=None`` keeps the exact pre-fencing behavior (every direct caller /
    test path)."""
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
            book_grade = book.grade
            expected_pdf_size = book.file_size_bytes  # R13 integrity guard
            # Per-job provider/model. Pinned at job-creation time so retries
            # hit the same backend; ``model`` may be None — agent._resolve_model
            # falls back to either a hardcoded provider default or the CLI's
            # own default in that case.
            provider = job.provider
            model = job.model
            # kind selects the phase flow (homework vs teacher_material) —
            # captured ONCE here, alongside provider/model, so the later
            # sequence-planning block never touches the possibly-detached
            # ORM `job` object.
            job_kind = getattr(job, "kind", "homework") or "homework"
            # Per-job auth transport: 'cli' (default) drives the CLI as today;
            # 'api' threads provider API keys via _auth_env and restricts
            # failover to the requested provider. Pinned at job creation.
            transport = getattr(job, "transport", "cli") or "cli"
            # Phase 4.1 §5: per-role transports. 'inherit' follows the job's
            # transport; an explicit 'cli'/'api' wins. Resolved ONCE here so
            # every downstream spawn routes deterministically.
            extract_transport = resolve_role_transport(
                getattr(job, "extract_transport", "inherit") or "inherit", transport
            )
            judge_transport = resolve_role_transport(
                getattr(job, "judge_transport", "inherit") or "inherit", transport
            )
            custom_prompts = getattr(job, "custom_prompts", None)
            selected_phases = getattr(job, "selected_phases", None)
            # Global launch defaults (DB row): the defensive fallback source for
            # any NULL judge/extract column. Loaded ONCE here, before first use.
            from app.repositories import launch_defaults as _ld_repo  # noqa: PLC0415
            _ld = await _ld_repo.get(session)
            # Per-job judge provider/model override: explicit columns let the
            # user steer who grades; NULL defensively falls back to the DB global
            # default (jobs are stamped at launch; this is belt-and-suspenders,
            # never reads settings). Self-grade is still hard-swapped downstream.
            judge_provider_ov = getattr(job, "judge_provider", None) or _ld.judge_provider
            judge_model_ov = getattr(job, "judge_model", None) or _ld.judge_model
            # CQ-C: per-role solver transport/provider/model — same 'inherit'
            # resolution + DB-global fallback pattern as the judge, above.
            solver_transport = resolve_role_transport(
                getattr(job, "solver_transport", "inherit") or "inherit", transport
            )
            solver_provider_ov = getattr(job, "solver_provider", None) or _ld.solver_provider
            solver_model_ov = getattr(job, "solver_model", None) or _ld.solver_model
            # Live-read boss-arena kill-switch off the already-loaded singleton
            # (operator-editable at /settings). Read once per job like the rest of
            # _ld; threaded into _execute_phase below.
            solver_boss_arena_enabled = _ld.solver_boss_arena_enabled
            # Per-job extract provider/model override: explicit columns win, else
            # the DB global default (via _ld). Content phases are UNAFFECTED —
            # they keep using job.provider / job.model.
            extract_provider, extract_model = _resolve_extract(
                getattr(job, "extract_provider", None),
                getattr(job, "extract_model", None),
                _ld,
            )
            # Session-limit strategy: resolve ONCE per job. Load the batch to
            # get the per-batch override; fall back to the fleet-wide env default
            # (settings.session_limit_strategy) via resolve_session_limit_strategy.
            # Lazy Batch import avoids any circular-import risk at module level.
            from app.models.batch import Batch as _Batch  # noqa: PLC0415
            _batch = await session.get(_Batch, job.batch_id) if job.batch_id else None
            session_limit_strategy: str = resolve_session_limit_strategy(
                _batch.session_limit_strategy if _batch else None
            )
            # Output language for this job (uz/en/ru). Captured once here to
            # avoid lazy-load / detachment surprises on later ORM access.
            job_output_language: str = getattr(job, "output_language", None) or "uz"
            section_data = {
                "id": section.id,
                "title": section.section_title,
                "number": section.section_number,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "chapter": section.chapter_title or "",
            }
            # Next teaching lesson in the book (by order_index), if any — used
            # to inject a curriculum-boundary note into lesson_context below so
            # content phases don't reach into next-lesson material (R21.1).
            _next = await toc_repo.get_next_in_book(session, book_id, section.order_index)
            next_lesson_title: Optional[str] = _next.section_title if _next else None

        # Local on-disk PDF; on a multi-PC fleet a worker may be missing it, so
        # fetch-on-demand from the head (R13). Sync helper off the event loop —
        # same idiom as read_whole_book_text below. Raises if it can't produce it.
        pdf_path = await asyncio.to_thread(
            book_fetch.ensure_book_pdf_sync, book_id, expected_pdf_size
        )

        log.info(
            f"[job {job_id}] context loaded | subject={subject} "
            f"provider={provider} model={model or '<default>'} "
            f"section={section_data['number']!r} title={section_data['title']!r} "
            f"pages={section_data['page_start']}-{section_data['page_end']}"
        )

        # ─── plan phase sequence (single flow — no classify/easy-hard) ──
        # Subset: job.selected_phases is the dependency-closure the endpoint stored.
        # Defensive re-order/filter against the live flow; None ⇒ full flow.
        full_flow = _plan_full_flow(job_kind, subject)
        if selected_phases:
            chosen = set(selected_phases)
            content_planned = [p for p in full_flow if p in chosen]
        else:
            content_planned = full_flow
        sequence: list[str] = ["extract", *content_planned]
        log.info(f"[job {job_id}] sequence planned | phases={sequence}")

        async with SessionLocal() as session:
            _r = await jobs_repo.set_status(
                session, job_id, "running", started_at=_utcnow(),
                claim_token=_token_of(lease),
            )
            await session.commit()
        _raise_on_lease_signal(_r)  # commit first (persist any lease/cancel event), then signal

        # pinned None — classify/easy-hard removed; kept in helper signatures to avoid wide surgery
        difficulty: Optional[str] = None
        prior_outputs: dict[str, str] = {}

        async with SessionLocal() as session:
            _existing_rows = await phase_repo.list_for_job(session, job_id)
        _done_md = _done_phase_md(_existing_rows)
        if _done_md:
            log.info(f"[job {job_id}] resume: {len(_done_md)} done phase(s) skipped: {sorted(_done_md)}")

        lesson_context: Optional[str] = None
        # PR-1/plan §10: the source map digest threaded into every content
        # phase prompt as the authoritative concept list (source fidelity).
        source_map_digest: str = ""
        # The set of legitimate concept ids from the source map. Used to detect
        # phases that cite invented ids (plan §10 "no invented facts").
        source_map_ids: set[str] = set()
        phase_order = 0

        file_phases = file_needed_phases(subject)
        log.info(
            f"[job {job_id}] file-needed phases for '{subject}': "
            f"{sorted(file_phases) or '(none beyond extract)'}"
        )

        # ─── head: extract (sequential — every content phase depends on it) ──
        # extract runs first because the next step's content depends on its
        # *output*: extract → lesson_context → source map → content phases.
        head_phases: list[str] = ["extract"]

        for idx, phase_name in enumerate(head_phases):
            if phase_name in _done_md:
                if phase_name == "extract":
                    lesson_context = _done_md["extract"]
                    log.info(f"[job {job_id}] resume: reused extract ({len(lesson_context)} chars)")
                continue
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
                    transport=transport,
                    extract_transport=extract_transport,
                    judge_transport=judge_transport,
                    solver_transport=solver_transport,
                    custom_prompts=custom_prompts,
                    judge_provider_ov=judge_provider_ov,
                    judge_model_ov=judge_model_ov,
                    solver_provider_ov=solver_provider_ov,
                    solver_model_ov=solver_model_ov,
                    solver_boss_arena_enabled=solver_boss_arena_enabled,
                    extract_provider=extract_provider,
                    extract_model=extract_model,
                    session_limit_strategy=session_limit_strategy,
                    output_language=job_output_language,
                    lease=lease,
                )
            except (LeaseLostSignal, CancelWonSignal):
                raise  # control signal — unwind to the worker, never a swallow
            except (SessionLimitPause, SlotSaturation, TransientPhaseError):
                raise  # propagate to worker — requeue/park, not a swallow
            except Exception:
                # _execute_one_phase already published the error event and
                # marked the job failed (hard class). We just unwind cleanly.
                return

            if phase_name == "extract":
                lesson_context = output_md
                log.info(f"[job {job_id}] lesson_context captured | chars={len(output_md)}")
                # PR-1: derive the structured source map from the freshly
                # captured lesson_context — text-only, pinned to the cheap
                # extractor (no PDF re-read). Best-effort: a failure logs but
                # does NOT fail the job (downstream phases don't consume the
                # map yet; that wiring lands in later PRs).
                # Source map dropped (md-per-phase reshape): grounding now lives
                # in each phase's own ## Source Extraction block. Keep the digest
                # empty so downstream phases get no injected map.
                source_map_digest = ""
                source_map_ids = set()
        content_phases = sequence[len(head_phases):]

        # Thread the student grade into lesson_context so the prompts' grade-band
        # rules have a value to read (book.grade otherwise never reached content
        # generation). Single point — covers both the fresh and resume paths.
        lesson_context = _inject_grade(lesson_context, book_grade)
        lesson_context = _inject_lesson_boundary(lesson_context, next_lesson_title)

        for _name, _md in _done_md.items():
            if _name not in head_phases:
                prior_outputs[_name] = _md

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
                    source_map_ids=source_map_ids,
                    transport=transport,
                    extract_transport=extract_transport,
                    judge_transport=judge_transport,
                    solver_transport=solver_transport,
                    custom_prompts=custom_prompts,
                    judge_provider_ov=judge_provider_ov,
                    judge_model_ov=judge_model_ov,
                    solver_provider_ov=solver_provider_ov,
                    solver_model_ov=solver_model_ov,
                    solver_boss_arena_enabled=solver_boss_arena_enabled,
                    extract_provider=extract_provider,
                    extract_model=extract_model,
                    session_limit_strategy=session_limit_strategy,
                    output_language=job_output_language,
                    lease=lease,
                )
            except (LeaseLostSignal, CancelWonSignal):
                raise  # control signal — unwind to the worker, never a swallow
            except (SessionLimitPause, SlotSaturation, TransientPhaseError):
                raise  # propagate to worker — requeue/park, not a swallow
            except RuntimeError as exc:
                if "content phase failed" in str(exc):
                    # _execute_one_phase already published the error and marked
                    # the job failed. Unwind cleanly without overwriting state.
                    return
                raise

        # Post-job coverage check (warn-only): does the packet cover the extract
        # contract? Rides the extract row's validation_warnings. Never fails a job.
        try:
            async with SessionLocal() as session:
                _rows = await phase_repo.list_for_job(session, job_id)
                _cov = _coverage_warnings_for_job(
                    [{"phase_name": r.phase_name, "output_md": r.output_md} for r in _rows])
                if _cov:
                    _ex = next((r for r in _rows if r.phase_name == "extract"), None)
                    if _ex is not None:
                        # dedupe: a tail-resume re-runs this hook, so drop any
                        # coverage warning already on the row (idempotent append).
                        _prev = list(_ex.validation_warnings or [])
                        _merged = _prev + [w for w in _cov if w not in _prev]
                        # guard=False: the extract row is already 'done' and the
                        # default guard (WHERE status != 'done') would no-op it.
                        # Deliberately token-LESS (D3): on a resumed job the
                        # extract row is a REUSED row carrying the PREVIOUS run's
                        # token, so a fenced write would miss and silently drop
                        # this advisory warning. This write is guard=False and
                        # fail-open — a stale worker appending an advisory warning
                        # is harmless, so fencing it buys no real safety.
                        await phase_repo.set_status(
                            session, _ex.id, _ex.status, validation_warnings=_merged,
                            guard=False)
                        await session.commit()
        except Exception as exc:  # noqa: BLE001 — advisory only, must never fail the job
            logger.warning(f"coverage check skipped (fail-open): {exc!r}")

        # No assembly — per-phase markdown in phase_outputs is the deliverable.
        # THE critical anti-double-completion fence: an obsolete worker whose job
        # was reclaimed must NEVER be able to mark it `done`. The fenced write
        # no-ops (LeaseLost) and the control signal unwinds before the completion
        # event / archive fire.
        async with SessionLocal() as session:
            _r = await jobs_repo.set_status(
                session, job_id, "done", completed_at=_utcnow(),
                claim_token=_token_of(lease),
            )
            await session.commit()
        _raise_on_lease_signal(_r)

        await events_bus.publish(
            resource_id,
            "job_completed",
            {"job_id": str(job_id), "download_url": f"/api/v1/jobs/{job_id}/download"},
        )

        try:
            # Fence the automatic archive on THIS run's winning claim_token
            # (Task 9): an obsolete worker whose job was reclaimed mid-flight
            # must not publish/stamp — see notion_archive._claim_token_ok.
            await notion_archive.archive_job(job_id, claim_token=_token_of(lease))
        except Exception:
            log.warning(f"[job {job_id}] notion archive hook failed (non-fatal)", exc_info=True)

        total_s = perf_counter() - t_start
        log.success(
            f"[job {job_id}] pipeline complete | phases_run={len(sequence)} "
            f"total_s={total_s:.1f}"
        )
        await _log_token_summary(job_id, log)

    except (LeaseLostSignal, CancelWonSignal):
        # Control signal (fenced job leases): a fenced write found the lease
        # lost or a cancel already finalized. NOT a content error — never mark
        # the job failed. Unwind to worker._execute_job after closing the bus.
        raise
    except (SessionLimitPause, SlotSaturation, TransientPhaseError):
        # Worker (Task 5) catches this and requeues/parks with a cooldown —
        # the job must NOT be marked failed here.  Propagate after closing
        # the SSE bus.
        raise
    except Exception as exc:
        total_s = perf_counter() - t_start
        log.exception(
            f"[job {job_id}] pipeline CRASHED after {total_s:.1f}s: {exc}"
        )
        async with SessionLocal() as session:
            _r = await jobs_repo.set_status(
                session, job_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
                claim_token=_token_of(lease),
            )
            await session.commit()
        # A reclaim (LeaseLost) or a cancel-win (CancelRequested) during this
        # terminal write means we no longer own the job — surface the control
        # signal instead of a spurious failure; never publish an error event.
        _raise_on_lease_signal(_r)
        await events_bus.publish(resource_id, "error", {"message": str(exc)})
    finally:
        await events_bus.close(resource_id)


async def _emit_started(resource_id: str, phase_name: str, phase_order: int) -> None:
    await events_bus.publish(
        resource_id,
        "phase_started",
        {"phase_name": phase_name, "phase_order": phase_order},
    )


async def _abandon_inflight(
    job_id: UUID, phase_names: list[str], status: str, reason: str,
    *, claim_token: Optional[UUID] = None,
) -> None:
    """Best-effort, cancellation-shielded reset of orphaned phase rows.
    Mirrors worker.py's shielded cancel-finalize craft: a cancellation
    delivered while this write runs must not kill the write.

    status='pending' when the JOB is being requeued/parked (transient /
    saturation / pause — rows are waiting); status='failed' on hard failure
    or user cancel (gate correction 4).

    ``claim_token`` fences the reset to phase rows THIS lease still owns (D2):
    when the heartbeat LOST-cancels this task (a real reclaim, or D1's false
    cancel), CancelledError propagates here — a token-less reset would clobber
    the NEW owner's reclaimed phase row. Threaded through to
    ``reset_abandoned_phases``; None keeps the legacy unfenced behavior."""
    if not phase_names:
        return
    async def _do() -> None:
        try:
            async with SessionLocal() as session:
                await phase_repo.reset_abandoned_phases(
                    session, [job_id],
                    phase_names=phase_names, status=status,
                    error_message=reason if status == "failed" else None,
                    claim_token=claim_token,
                )
                await session.commit()
        except Exception:
            logger.exception(f"[job {job_id}] abandoned-phase reset failed")
    task = asyncio.create_task(_do())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        pass


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
    transport: str = "cli",
    extract_transport: str = "cli",
    judge_transport: str = "cli",
    solver_transport: str = "cli",
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    solver_provider_ov: Optional[str] = None,
    solver_model_ov: Optional[str] = None,
    solver_boss_arena_enabled: bool = True,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
    output_language: str = "uz",
    lease: Optional[JobLease] = None,
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
        if phase_name == "teacher-deck":
            # Teacher-material deliverable: a single schema-validated call
            # persisted as content_json, not routed through _execute_phase's
            # content-phase branch (no judge/solver/lint yet, no markdown
            # fallback — see _execute_teacher_deck_phase's docstring). Uses
            # ONLY the locals _execute_one_phase already received (provider/
            # model/transport/output_language), which `run()` captured once
            # from the ORM `job` up front — no late re-read.
            output_md, tin, tout, _ph, parsed_struct = await _execute_teacher_deck_phase(
                job_id=job_id,
                phase_order=phase_order,
                subject=subject,
                provider=provider,
                model=model,
                lesson_context=lesson_context,
                transport=transport,
                session_limit_strategy=session_limit_strategy,
                output_language=output_language,
                lease=lease,
            )
        else:
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
                transport=transport,
                extract_transport=extract_transport,
                judge_transport=judge_transport,
                solver_transport=solver_transport,
                custom_prompts=custom_prompts,
                judge_provider_ov=judge_provider_ov,
                judge_model_ov=judge_model_ov,
                solver_provider_ov=solver_provider_ov,
                solver_model_ov=solver_model_ov,
                solver_boss_arena_enabled=solver_boss_arena_enabled,
                extract_provider=extract_provider,
                extract_model=extract_model,
                session_limit_strategy=session_limit_strategy,
                output_language=output_language,
                lease=lease,
            )
    except (LeaseLostSignal, CancelWonSignal):
        raise  # control signal — never a content failure / job-failed write
    except SessionLimitPause:
        raise  # worker requeues — job must NOT be marked failed
    except SlotSaturation:
        raise  # worker parks with cooldown — job must NOT be marked failed
    except Exception as exc:
        phase_ms = (perf_counter() - t_phase) * 1000
        msg = _phase_error_message(phase_name, exc)
        log.exception(
            f"[job {job_id}] phase '{phase_name}' FAILED after {phase_ms:.0f}ms: {msg}"
        )
        # Marker fallback (gate correction 1): saturation errors that BYPASSED
        # _run_with_failover — the scanned-PDF vision extract (pipeline.py:1113)
        # or any future direct agent call — must still park, never burn retries.
        if is_slot_saturation(exc):
            raise SlotSaturation(_error_text(exc)) from exc
        if _requeue_worthy(exc):
            # Do NOT mark failed here — propagate so the worker applies the
            # bounded queue retry (mark_failed_with_retry, queue-correctness-1).
            # Event publish is best-effort AFTER the decision: a broken bus
            # must not eat the signal (gate correction 2).
            await _publish_error_event(
                resource_id, {"phase_name": phase_name, "message": msg}
            )
            raise TransientPhaseError(msg) from exc
        # Hard failure: DB write FIRST (the terminal mark is the contract),
        # event publish best-effort afterwards (gate correction 2).
        async with SessionLocal() as session:
            _r = await jobs_repo.set_status(
                session, job_id, "failed",
                completed_at=_utcnow(),
                error_message=msg,
                claim_token=_token_of(lease),
            )
            await session.commit()
        # A reclaim / cancel-win during the fenced fail write means the job is
        # no longer ours — raise the control signal instead of the content
        # failure, and skip the error event.
        _raise_on_lease_signal(_r)
        await _publish_error_event(
            resource_id, {"phase_name": phase_name, "message": msg}
        )
        raise

    phase_ms = (perf_counter() - t_phase) * 1000
    log.success(
        f"[job {job_id}] phase '{phase_name}' done | "
        f"output_chars={len(output_md)} tokens_in={tin} tokens_out={tout} "
        f"duration_ms={phase_ms:.0f}"
    )
    # Refetch invariant (events_bus): the phase row (status=done + output_md)
    # is committed inside _execute_phase above BEFORE this publish — an
    # oversized payload's __refetch__ marker makes the SSE endpoint re-read
    # it. Do not reorder publish before commit.
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
    source_map_ids: Optional[set[str]] = None,
    transport: str = "cli",
    extract_transport: str = "cli",
    judge_transport: str = "cli",
    solver_transport: str = "cli",
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    solver_provider_ov: Optional[str] = None,
    solver_model_ov: Optional[str] = None,
    solver_boss_arena_enabled: bool = True,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
    output_language: str = "uz",
    lease: Optional[JobLease] = None,
) -> None:
    """Wave-based parallel scheduler for content phases.

    Each phase declares its deps in `flows.PHASE_DEPS`. We launch every phase
    whose deps are satisfied, then wait for the next completion, update
    prior_outputs, and re-launch newly-ready phases. Repeats until all phases
    have completed or one fails.

    Phase order (used by the frontend to display curriculum-order rows) stays
    stable: it's the position in `content_phases` plus the head offset.
    """
    pending: set[str] = _pending_phases(content_phases, prior_outputs)
    in_flight: dict[str, asyncio.Task] = {}
    phase_order_map: dict[str, int] = {
        name: phase_order_offset + i for i, name in enumerate(content_phases)
    }

    def _ready(name: str) -> bool:
        deps = resolve_phase_deps(name, content_phases)
        return deps.issubset(prior_outputs.keys())

    failed = False

    try:
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
                            transport=transport,
                            extract_transport=extract_transport,
                            judge_transport=judge_transport,
                            solver_transport=solver_transport,
                            custom_prompts=custom_prompts,
                            judge_provider_ov=judge_provider_ov,
                            judge_model_ov=judge_model_ov,
                            solver_provider_ov=solver_provider_ov,
                            solver_model_ov=solver_model_ov,
                            solver_boss_arena_enabled=solver_boss_arena_enabled,
                            extract_provider=extract_provider,
                            extract_model=extract_model,
                            session_limit_strategy=session_limit_strategy,
                            output_language=output_language,
                            lease=lease,
                        ),
                        name=f"phase:{name}",
                    )

            if not in_flight:
                if pending and not failed:
                    raise RuntimeError(
                        _scheduler_stuck_message(pending, content_phases)
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
                except (LeaseLostSignal, CancelWonSignal):
                    # Control signal (fenced job leases): the lease was lost or a
                    # cancel already finalized. Cancel + drain in-flight peers as
                    # LOCAL cleanup only (NO phase-row writes — the job is no
                    # longer ours to mutate, unlike the pause/failed branches
                    # which call _abandon_inflight) and re-raise so pipeline.run
                    # unwinds to the worker.
                    for peer in in_flight.values():
                        peer.cancel()
                    if in_flight:
                        await asyncio.gather(*in_flight.values(), return_exceptions=True)
                        in_flight.clear()
                    raise
                except (SessionLimitPause, SlotSaturation, TransientPhaseError):
                    # Cancel in-flight peers, drain, then propagate so the worker
                    # can requeue with a cooldown.  Do NOT set failed=True — the
                    # job must NOT be marked failed on a pause.
                    abandoned = list(in_flight.keys())
                    for peer in in_flight.values():
                        peer.cancel()
                    if in_flight:
                        await asyncio.gather(*in_flight.values(), return_exceptions=True)
                        in_flight.clear()
                    await _abandon_inflight(
                        job_id, abandoned, "pending", "abandoned: job requeued",
                        claim_token=_token_of(lease),
                    )
                    raise
                except Exception:
                    # Already logged + marked failed by _execute_one_phase. Cancel
                    # any peers still in flight and stop launching new phases.
                    failed = True
                    abandoned = list(in_flight.keys())
                    for peer in in_flight.values():
                        peer.cancel()
                    # Drain cancellations so we don't leak tasks
                    if in_flight:
                        await asyncio.gather(*in_flight.values(), return_exceptions=True)
                        in_flight.clear()
                    await _abandon_inflight(
                        job_id, abandoned, "failed", "abandoned: sibling phase failed",
                        claim_token=_token_of(lease),
                    )
                    continue

                prior_outputs[phase_name] = output_md
    except asyncio.CancelledError:
        # External cancel (user pressed Cancel). asyncio.wait() does NOT cancel
        # its awaitables, so we must cancel every in-flight phase and gather
        # them - that lets each _execute_phase -> _spawn run its
        # `except CancelledError: kill_tree(...)` before we unwind.
        abandoned = list(in_flight.keys())
        for t in in_flight.values():
            t.cancel()
        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)
            in_flight.clear()
        await _abandon_inflight(
            job_id, abandoned, "failed", "abandoned: job cancelled",
            claim_token=_token_of(lease),
        )
        raise

    if failed:
        # Caller's surrounding try/except will see the original exception was
        # already published; raise a sentinel so it returns cleanly.
        raise RuntimeError("content phase failed")


def _failover_chain(requested_provider: str) -> list[str]:
    """Requested provider first, then settings.failover_provider_order, skipping
    the requested one and any dup. claude is absent from the configured order, so
    a claude job tries claude first but never falls *back* to claude."""
    chain = [requested_provider]
    for p in settings.failover_provider_order:
        if p != requested_provider and p not in chain:
            chain.append(p)
    return chain


# Same-provider retry budget per failure class before moving to the next provider.
_SAME_RETRY_BUDGET = {"transient": 2, "hard": 1, "wall": 0}


async def _run_with_failover(
    *,
    requested_provider: str,
    model: Optional[str],
    run_fn,
    transport: str = "cli",
    session_limit_strategy: str = "pause",
):
    """Run a phase across the failover chain. `run_fn(provider, model)` returns
    (output_md, tokens_in, tokens_out). On failure, classify → retry same (per
    budget, exp backoff) or move to the next provider. Each attempt is bounded by
    settings.per_attempt_timeout_seconds (kills a hung CLI). Fallback providers
    get model=None (the job's model is provider-specific; None → provider default,
    preserving the _resolve_model no-leak invariant). Returns
    (output_md, tin, tout, produced_by); raises the last error when all fail.

    api transport does NOT cross-provider failover: the fallback providers
    (codex/kimi/opencode) have no api support, and an api-claude→api-gemini
    fall would run model=None (violating the explicit-model rule + mis-pricing).
    The chain is pinned to the requested provider; same-provider retry budgets
    still apply.

    session_limit_strategy ('pause' | 'switch'):
      - 'switch': session-limit error advances to the next provider immediately
        (treated as a wall — budget 0, no same-provider retry). The existing
        chain logic carries it to codex/gemini.
      - 'pause': session-limit error raises SessionLimitPause (with the parsed
        reset time) immediately — the chain is aborted; the worker requeues the
        job with a cooldown until reset.
    The is_session_limit check runs BEFORE failure_classifier.classify so that
    a "usage limit reached · resets …" string is intercepted here, not treated
    as a wall (which would silently skip the pause strategy)."""
    chain = _failover_chain(requested_provider)
    if transport == "api":
        chain = [requested_provider]
    last_exc: Optional[Exception] = None
    for prov in chain:
        # Skip a FALLBACK provider whose CLI isn't installed on this worker —
        # trying it only raises a confusing "<prov> CLI not found" and burns the
        # attempt (the R13 single-CLI-worker failure). The REQUESTED provider is
        # never skipped: a missing requested CLI is a real error worth surfacing.
        # (fleet-failover-1)
        if prov != requested_provider and not agent.provider_cli_installed(prov):
            continue
        attempt_model = model if prov == requested_provider else None
        same = 0
        while True:
            try:
                out, tin, tout = await asyncio.wait_for(
                    run_fn(prov, attempt_model),
                    timeout=settings.per_attempt_timeout_seconds,
                )
                return out, tin, tout, prov
            except asyncio.TimeoutError:
                # Attempt blew per_attempt_timeout — hung/too-slow provider.
                # Wrap in a typed, NON-BLANK error (str(asyncio.TimeoutError())
                # is '' → the blank "<phase>: " error_message bug). Fail over
                # immediately (no same-provider retry) exactly as before.
                last_exc = PhaseAttemptTimeout(
                    f"per-attempt timeout after "
                    f"{settings.per_attempt_timeout_seconds}s "
                    f"(provider={prov}, transport={transport})"
                )
                break
            except Exception as exc:  # noqa: BLE001 — classify, don't swallow
                # Fleet credential-slot exhaustion: park the job (worker
                # requeues with cooldown) — never classify, never retry,
                # never mark failed. Checked BEFORE is_session_limit/classify
                # for the same reason the session-limit check precedes
                # classify: the '429 …' text would otherwise be misrouted.
                if is_slot_saturation(exc):
                    raise SlotSaturation(str(exc))
                # ── Session-limit check MUST run BEFORE failure_classifier.classify ──
                # "usage limit reached · resets Xam" matches _WALL in classify()
                # (contains "limit" + "reached"). If classify ran first, the pause
                # strategy would never fire.  (gatekeeper #1)
                if failure_classifier.is_session_limit(str(exc)):
                    if session_limit_strategy == "pause":
                        raise SessionLimitPause(
                            failure_classifier.parse_session_limit_reset(
                                str(exc), now=_utcnow()
                            )
                        )
                    # switch: treat as wall (budget=0) → advance to next provider
                    last_exc = exc
                    break
                budget = _SAME_RETRY_BUDGET[failure_classifier.classify(exc)]
                if same < budget:
                    same += 1
                    await asyncio.sleep(2 ** same)  # ~2s, ~4s — slot already released
                    continue
                last_exc = exc
                break  # exhausted this provider → next in chain
    raise last_exc or RuntimeError(f"{requested_provider}: all providers exhausted")


# ─────────────────────────────────────────────────────────────────────
# Artifact generation: structured attempt → typed markdown fallback
#
# Every content-generation call site (initial, judge regen, solver regen) goes
# through `_generate_artifact`, so the markdown and the JSON that produced it
# can never drift apart: a regen replaces the WHOLE artifact, never output_md
# alone.
# ─────────────────────────────────────────────────────────────────────


async def _run_markdown_attempt(
    *,
    phase_name: str,  # noqa: ARG001 — symmetry with _run_structured_attempt
    requested_provider: str,
    model: Optional[str],
    run_fn,
    transport: str = "cli",
    session_limit_strategy: str = "pause",
    **_structured_only,
) -> tuple[str, Optional[int], Optional[int], str]:
    """Today's markdown generation path, extracted verbatim.

    It is exactly ``_run_with_failover``: classify → retry same → next provider.
    Pulled out as a named seam so `_generate_artifact` has one thing to fall
    back to (and tests have one thing to monkeypatch) while the nine phases
    without a structured schema keep byte-identical behaviour.
    """
    return await _run_with_failover(
        requested_provider=requested_provider,
        model=model,
        run_fn=run_fn,
        transport=transport,
        session_limit_strategy=session_limit_strategy,
    )


# Carried in `run_fn`'s first tuple slot to signal "the model could not author
# this config" WITHOUT raising into _run_with_failover (which would classify it
# as a transport fault and burn the retry/failover budget). A unique object(),
# never a truthy/falsey value, so `is` identity is the only way to match it.
_SCHEMA_EXHAUSTED = object()


async def _run_structured_attempt(
    *,
    phase_name: str,
    structured_prompt: Optional[str],
    requested_provider: str,
    model: Optional[str],
    transport: str = "cli",
    session_limit_strategy: str = "pause",
    lesson_context: Optional[str] = None,
    prior_outputs: Optional[dict[str, str]] = None,
    difficulty: Optional[str] = None,
    attachments: Optional[list[Path]] = None,
    job_id: Optional[UUID] = None,
    po_id: Optional[UUID] = None,
    source_map_digest: str = "",
    **_markdown_only,
) -> tuple[PhaseArtifact, Optional[int], Optional[int], str]:
    """One JSON-authoring call, validated into a `PhaseArtifact`.

    Runs through ``_run_with_failover`` exactly like the markdown lane, so a
    structured call keeps the per-attempt timeout, slot-saturation parking,
    error classification, same-provider retry and cross-provider failover.
    Calling ``agent.run_phase`` directly (the shape this replaced) silently
    dropped all of it — there is no layer above that restores it.

    Schema exhaustion must NOT reach the failover driver as an exception: that
    driver classifies and retries, which would burn the budget on a model that
    simply cannot produce this config. So ``agent.SchemaValidationExhausted``
    (and a ``parsed is None`` result) is converted into the ``_SCHEMA_EXHAUSTED``
    sentinel, returned in ``run_fn``'s first slot, and re-raised here as a
    `StructuredPhaseError` — the one signal `_generate_artifact` falls back on.
    Every OTHER exception still escapes ``run_fn`` normally and keeps the full
    classify/retry/failover semantics.
    """
    if not structured_prompt:
        raise StructuredPhaseError(f"no structured prompt for phase '{phase_name}'")
    schema = SCHEMAS[phase_name]

    async def _structured_run(prov: str, mdl: Optional[str]):
        try:
            result = await agent.run_phase(
                provider=prov,
                model=mdl,
                phase_prompt=structured_prompt,
                phase_name=phase_name,
                homework_job_id=job_id,
                phase_output_id=po_id,
                lesson_context=lesson_context,
                prior_outputs=prior_outputs,
                attachments=list(attachments or []),
                schema=schema,
                difficulty=difficulty,
                max_output_tokens=max_output_tokens_for(phase_name),
                source_map_digest=source_map_digest,
                transport=transport,
            )
        except (agent.SchemaValidationExhausted, ValidationError):
            # Returned, NOT raised: keeps _run_with_failover from classifying
            # and retrying a model that cannot produce this config.
            return _SCHEMA_EXHAUSTED, None, None
        if result.parsed is None:
            return _SCHEMA_EXHAUSTED, None, None
        tin_ = int(result.usage.get("prompt_tokens") or 0) or None
        tout_ = int(result.usage.get("output_tokens") or 0) or None
        return result.parsed, tin_, tout_

    parsed, tin, tout, produced_by = await _run_with_failover(
        requested_provider=requested_provider,
        model=model,
        run_fn=_structured_run,
        transport=transport,
        session_limit_strategy=session_limit_strategy,
    )
    if parsed is _SCHEMA_EXHAUSTED:
        raise StructuredPhaseError(
            f"{schema.__name__}: model could not produce a valid config"
        )
    # artifact_from_config may itself raise StructuredPhaseError (renderer
    # refused / produced nothing) — that is a fallback trigger, by design.
    return artifact_from_config(phase_name, parsed), tin, tout, produced_by


async def _run_teacher_deck_attempt(
    *,
    structured_prompt: str,
    requested_provider: str,
    model: Optional[str],
    transport: str = "cli",
    session_limit_strategy: str = "pause",
    lesson_context: Optional[str] = None,
    job_id: Optional[UUID] = None,
    po_id: Optional[UUID] = None,
) -> tuple[Any, Optional[int], Optional[int], str]:
    """The teacher-deck sibling of ``_run_structured_attempt``: same resilience
    (``_run_with_failover`` — per-attempt timeout, ``SlotSaturation`` parking,
    session-limit pause, same-provider retry) around one schema-validated
    ``agent.run_phase`` call, but deliberately does NOT call
    ``artifact_from_config`` at the end — there is no teacher-deck markdown
    renderer, so that call would raise ``StructuredPhaseError``. Returns the
    parsed ``TeacherDeck`` model directly.

    Reuses the same ``_SCHEMA_EXHAUSTED`` sentinel technique as
    ``_run_structured_attempt`` so a model that genuinely cannot produce this
    config isn't burned through the classify/retry/failover budget a second
    time — but where the content lane converts that into a (fallback-able)
    ``StructuredPhaseError``, this re-raises ``agent.SchemaValidationExhausted``
    UNCONVERTED: there is no markdown fallback for teacher-deck, so schema
    exhaustion must fail the phase loudly instead of being caught anywhere.
    """
    schema = SCHEMAS["teacher-deck"]

    async def _structured_run(prov: str, mdl: Optional[str]):
        try:
            result = await agent.run_phase(
                provider=prov,
                model=mdl,
                phase_prompt=structured_prompt,
                phase_name="teacher-deck",
                homework_job_id=job_id,
                phase_output_id=po_id,
                lesson_context=lesson_context,
                schema=schema,
                max_output_tokens=max_output_tokens_for("teacher-deck"),
                transport=transport,
            )
        except (agent.SchemaValidationExhausted, ValidationError):
            return _SCHEMA_EXHAUSTED, None, None
        if result.parsed is None:
            return _SCHEMA_EXHAUSTED, None, None
        tin_ = int(result.usage.get("prompt_tokens") or 0) or None
        tout_ = int(result.usage.get("output_tokens") or 0) or None
        return result.parsed, tin_, tout_

    parsed, tin, tout, produced_by = await _run_with_failover(
        requested_provider=requested_provider,
        model=model,
        run_fn=_structured_run,
        transport=transport,
        session_limit_strategy=session_limit_strategy,
    )
    if parsed is _SCHEMA_EXHAUSTED:
        raise agent.SchemaValidationExhausted(
            f"{schema.__name__}: model could not produce a valid teacher-deck config"
        )
    return parsed, tin, tout, produced_by


async def _execute_teacher_deck_phase(
    *,
    job_id: UUID,
    phase_order: int,
    subject: str,
    provider: str,
    model: Optional[str],
    lesson_context: Optional[str],
    transport: str = "cli",
    session_limit_strategy: str = "pause",
    output_language: str = "uz",
    lease: Optional[JobLease] = None,
) -> tuple[str, Optional[int], Optional[int], str, Optional[Any]]:
    """Generate the whole teacher-deck lesson-plan in ONE schema-validated call
    and persist it as ``content_json`` — the teacher-material sibling of
    ``_execute_phase``'s content-phase branch, called from ``_execute_one_phase``
    instead of it (not routed through it):

    - ALWAYS structured. Bypasses ``settings.structured_output_enabled`` (the
      content lane's kill switch) and its markdown fallback entirely — there is
      no teacher-deck markdown renderer, and ``_generate_artifact``'s fallback
      path would call ``artifact_from_config`` and raise
      ``StructuredPhaseError``.
    - No judge / no solver / no content_lint here — the dedicated fidelity gate
      is a separate, later phase-execution task; this only wires generation +
      persistence.
    - ``get_prompt_hash`` is NOT used: it hashes ``get_prompt`` (the markdown
      glob under ``prompts/_general/*.md``), which has no ``teacher-deck.md``
      and raises ``KeyError``. The structured prompt lives under
      ``structured/`` instead, so its own content is hashed directly.
    - On ``SchemaValidationExhausted`` (via ``_run_teacher_deck_attempt``), the
      phase row is marked ``failed`` and the exception re-raised — mirroring
      ``_execute_phase``'s generic-exception branch exactly, so
      ``_execute_one_phase``'s existing error handling marks the JOB failed too
      (no markdown to degrade to).
    """
    _token = _token_of(lease)
    structured_prompt = get_structured_prompt(
        subject, "teacher-deck", output_language=output_language
    )
    if not structured_prompt:
        raise StructuredPhaseError("no structured prompt for phase 'teacher-deck'")
    prompt_hash = "structured:sha256:" + hashlib.sha256(
        structured_prompt.encode("utf-8")
    ).hexdigest()
    phase_model_label = model or "<provider-default>"

    async with SessionLocal() as session:
        po = await phase_repo.create_or_reset(
            session,
            job_id=job_id,
            phase_name="teacher-deck",
            phase_order=phase_order,
            prompt_hash=prompt_hash,
            model_name=phase_model_label,
            lease=lease,
        )
        if po is LeaseLost:
            raise LeaseLostSignal()
        po_id = po.id
        _pr = await phase_repo.set_status(
            session, po_id, "running", started_at=_utcnow(), claim_token=_token,
        )
        _jr = await jobs_repo.set_status(
            session, job_id, "running", current_phase="teacher-deck", claim_token=_token,
        )
        await session.commit()
        _raise_on_lease_signal(_pr)
        _raise_on_lease_signal(_jr)

    try:
        deck, tin, tout, produced_by = await _run_teacher_deck_attempt(
            structured_prompt=structured_prompt,
            requested_provider=provider,
            model=model,
            transport=transport,
            session_limit_strategy=session_limit_strategy,
            lesson_context=lesson_context,
            job_id=job_id,
            po_id=po_id,
        )
    except (LeaseLostSignal, CancelWonSignal):
        raise  # control signal — never a phase-failed write (job is not ours)
    except SessionLimitPause:
        raise  # propagate to worker — phase must NOT be marked failed on a pause
    except Exception as exc:
        # Mirrors _execute_phase's generic-exception branch exactly, including
        # SlotSaturation (raised by _run_with_failover) — it is NOT special-cased
        # here either, same as the content lane: the phase row is marked failed,
        # then the signal re-raises past this point for _execute_one_phase to
        # re-classify and avoid marking the JOB failed on a park/pause.
        async with SessionLocal() as session:
            _fr = await phase_repo.set_status(
                session, po_id, "failed",
                completed_at=_utcnow(),
                error_message=_error_text(exc),
                claim_token=_token,
            )
            await session.commit()
        _raise_on_lease_signal(_fr)
        raise

    async with SessionLocal() as session:
        _dr = await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md="",
            tokens_input=tin,
            tokens_output=tout,
            provider=produced_by,
            content_json=deck.model_dump(),
            authoring_mode="structured",
            content_schema_version=TeacherDeck.SCHEMA_VERSION,
            claim_token=_token,
        )
        await session.commit()
    _raise_on_lease_signal(_dr)

    return "", tin, tout, prompt_hash, deck


async def _generate_artifact(
    *, phase_name: str, is_custom: bool = False, **kw
) -> tuple[PhaseArtifact, Optional[int], Optional[int], str]:
    """Structured attempt, then markdown fallback ONLY on StructuredPhaseError.

    Any other exception (auth, 429, slot saturation, timeout, network)
    propagates untouched so the existing classify/retry/failover logic still
    applies. Widening this catch would silently convert a transport outage into
    a "the model can't do JSON" fallback and hide real breakage.

    A custom uploaded prompt is a MARKDOWN contract (it is what the judge, the
    solver and the lint all read), so it disables the structured lane entirely
    and records ``markdown_custom``.

    ``settings.structured_output_enabled`` (default False) is the global kill
    switch: while off, no phase attempts JSON-authoring and every phase renders
    markdown_builtin, regardless of SCHEMAS membership — this keeps the
    content_json lane dark in production until it is flipped on deliberately.
    """
    structured = (
        phase_name in SCHEMAS
        and not is_custom
        and settings.structured_output_enabled
    )
    if structured:
        try:
            return await _run_structured_attempt(phase_name=phase_name, **kw)
        except StructuredPhaseError as exc:
            logger.warning(
                f"[{phase_name}] structured generation failed ({exc}); "
                f"falling back to markdown"
            )
    md, tin, tout, produced_by = await _run_markdown_attempt(phase_name=phase_name, **kw)
    if is_custom:
        mode = "markdown_custom"
    elif structured:
        mode = "markdown_fallback"
    else:
        mode = "markdown_builtin"
    return artifact_from_markdown(md, mode=mode), tin, tout, produced_by


async def _judge_with_timeout(**kwargs) -> phase_judge.JudgeOutcome:
    """Wrap phase_judge.judge in the per-attempt timeout.

    On asyncio.TimeoutError the outcome degrades to judge-unavailable (same
    shape phase_judge.judge itself produces on any CLI/parse error) so the
    phase completes `done` and the job is NOT failed.  A TimeoutError is NOT
    an auth error, so the existing _is_auth_error re-raise in _execute_phase
    is unaffected — this helper swallows only the timeout.
    """
    try:
        return await asyncio.wait_for(
            phase_judge.judge(**kwargs),
            timeout=settings.per_attempt_timeout_seconds,
        )
    except asyncio.TimeoutError:
        return phase_judge.JudgeOutcome(
            available=False,
            passed=True,
            warnings=["judge-unavailable: TimeoutError"],
            feedback="",
        )


def _custom_for(phase_name: str, custom_prompts: Optional[dict]) -> Optional[str]:
    """The stripped custom prompt for this phase, or None (blank/missing).
    `extract` is never overridden — callers pass it through harmlessly."""
    c = (custom_prompts or {}).get(phase_name)
    return c if (c and c.strip()) else None


def _error_text(exc: BaseException) -> str:
    """Non-blank error text: str(exc) with repr fallback (asyncio.TimeoutError
    stringifies to ''). Shared by the JOB-row and PHASE-row writes."""
    return str(exc).strip() or repr(exc)


def _phase_error_message(phase_name: str, exc: BaseException) -> str:
    """'<phase>: <reason>' with a guaranteed non-blank reason."""
    return f"{phase_name}: {_error_text(exc)}"


async def _publish_error_event(resource_id: str, payload: dict) -> None:
    """Best-effort error-event publish (gate correction 2): a broken events
    bus must NEVER swallow the failure signal — the DB write / typed raise is
    the source of truth, the event is advisory UI."""
    try:
        await events_bus.publish(resource_id, "error", payload)
    except Exception:
        logger.exception(f"error-event publish failed for {resource_id} (non-fatal)")


def _requeue_worthy(exc: BaseException) -> bool:
    """Transient-only queue-retry policy (user-locked 2026-07-20): attempt
    timeouts, rate-limit 429s, and transient net errors get the bounded
    queue retry; hard errors and walls stay terminal (retries bill real $)."""
    if isinstance(exc, PhaseAttemptTimeout):
        return True
    if agent._is_rate_limited(str(exc)):
        return True
    return failure_classifier.classify(exc) == "transient"


async def _verify_source_for_section(pdf_path, book_text: str, section: dict) -> str:
    """R2 (money-rule guard): bound the fidelity VERIFY call's source to the
    lesson's own pages (±1) instead of the whole book_text (0035's normal path
    ships the entire book). The section's worked examples live on its pages.
    Falls back to the full book_text if the page read yields nothing."""
    ps, pe = section.get("page_start"), section.get("page_end")
    if not ps or not pe:
        return book_text
    try:
        scoped = await asyncio.to_thread(
            agent.read_page_range_text, pdf_path, ps, pe, margin=1
        )
    except Exception:
        return book_text
    return scoped or book_text


async def _lesson_source_or_none(pdf_path, section: dict) -> "str | None":
    """STRICT lesson-scoped source text for the completeness check: the lesson's
    own printed pages (±1), or None.

    Deliberately has NO whole-book fallback — unlike _verify_source_for_section.
    A completeness check handed the whole book would enumerate every OTHER
    lesson's items and report them as omissions, so 'no usable window' must mean
    'do not run the check', never 'check against everything'. A window that
    fails Gate A (scanned / garbled text layer) is likewise unusable.

    Note the vision-extract path is *usually*, not always, excluded by this:
    the vision route triggers on WHOLE-BOOK Gate A / density (pipeline.py:1502),
    while this re-applies Gate A to the lesson WINDOW. A mixed book whose window
    does carry a real text layer will still be checked — which is correct, since
    the window is then genuinely readable."""
    ps, pe = section.get("page_start"), section.get("page_end")
    if not ps or not pe:
        return None
    try:
        text = await asyncio.to_thread(
            agent.read_page_range_text, pdf_path, ps, pe, margin=1
        )
    except Exception as exc:  # noqa: BLE001 — advisory path, never fail a job
        logger.warning(f"extract coverage: source read failed (fail-open): {exc!r}")
        return None
    if not (text or "").strip() or agent.validate_extract_text(text) is not None:
        return None
    return text


def _extract_coverage_warnings(misses: list) -> list[str]:
    """Format completeness findings as ONE advisory warning string (or none).

    Central items come first so a truncated read still shows what matters. The
    `extract_coverage:` prefix is deliberately distinct from `lint:` (which
    marks deterministic checks) — this one costs a model call."""
    labels = [(m.label or "").strip()[:80] for m in misses if (m.label or "").strip()]
    if not labels:
        return []
    ordered = (
        [(m.label or "").strip()[:80] for m in misses if m.central and (m.label or "").strip()]
        + [(m.label or "").strip()[:80] for m in misses if not m.central and (m.label or "").strip()]
    )
    n_central = sum(1 for m in misses if m.central and (m.label or "").strip())
    cap = max(1, settings.extract_coverage_max_items)
    shown = "; ".join(ordered[:cap])
    more = f" (+{len(ordered) - cap} more)" if len(ordered) > cap else ""
    return [
        f"extract_coverage: {len(ordered)} item(s) the lesson teaches are absent "
        f"from the extract ({n_central} central): {shown}{more}"
    ]


async def _check_extract_coverage(
    *, output_md: str, pdf_path, section: dict, provider: str, model,
    transport: str, job_id, po_id,
) -> list[str]:
    """WARN-ONLY completeness check: does the produced extract capture what the
    SOURCE lesson teaches? Returns advisory warning strings (possibly empty).

    This is the ONLY check in the stack that reads the source rather than
    trusting the extract — the judge grades every packet against the extract as
    ground truth (`phase_judge._FIDELITY_RULE`), so an under-summarizing extract
    is otherwise invisible to every downstream check.

    Fail-open on everything EXCEPT the lease/cancel control signals: those mean
    this worker no longer owns the job, and swallowing one would let an obsolete
    worker carry on writing. Slot saturation and session-limit pauses ARE
    swallowed here — parking a job whose extract already succeeded, over an
    advisory check, would cost more than the check is worth."""
    if not settings.extract_coverage_check_enabled:
        return []
    try:
        source = await _lesson_source_or_none(pdf_path, section)
        if source is None:
            logger.info(
                f"[job {job_id}] extract coverage: skipped (no usable lesson source text)"
            )
            return []
        # Bounded independently: this call sits OUTSIDE _run_with_failover's
        # asyncio.wait_for (pipeline.py:1013), so on a cli-transport extract
        # nothing else would stop a hung subprocess from stalling the job's
        # sequential head phase.
        misses = await asyncio.wait_for(
            agent.check_extract_coverage(
                summary=output_md, source_text=source,
                section_title=section.get("title") or "",
                section_number=section.get("number") or "",
                provider=provider,
                model=settings.extract_coverage_model or model,
                transport=transport,
                homework_job_id=job_id, phase_output_id=po_id,
            ),
            timeout=settings.extract_coverage_timeout_seconds,
        )
    except (LeaseLostSignal, CancelWonSignal):
        raise
    except Exception as exc:  # noqa: BLE001 — advisory: never fail/park a job
        logger.warning(
            f"[job {job_id}] extract coverage check skipped (fail-open): {exc!r}"
        )
        return []
    out = _extract_coverage_warnings(misses)
    if out:
        logger.warning(f"[job {job_id}] {out[0]}")
    return out


# Subject families where the fidelity guard's bare-parenthesis arm mostly
# catches prose glosses — (likes/dislikes), (*was/were*), (tale/narration) —
# rather than digitless algebra, so the strict predicate (digit OR '=') is
# applied to cut that noise. Measured on the corpus: english = 26 gloss
# candidates / 8 lessons, history = 27, geografiya = 8 — all pure noise, zero
# billed (these ran 2026-06-24, before the guard shipped 2026-07-02, so no
# agent_usages rows exist to have wasted spend). Family `default` (musiqa,
# tasviriy-sanat, texnologiya, informatika) deliberately stays OUT — it keeps
# today's noisy behavior. `informatika` is legitimately non-strict (code
# tokens are genuine '/'+paren content); music/fine-arts/technology are as
# gloss-prone as humanities but have no corpus data yet — fail toward current
# behavior rather than guess.
_STRICT_FIDELITY_FAMILIES = frozenset({"languages", "humanities"})


async def _verify_and_maybe_regen_extract(
    *, out: str, book_text: str, pdf_path, prov: str, mdl, transport: str,
    section: dict, job_id, po_id, subject: str = "",
) -> tuple[str, int, int]:
    """Item 1 guard: free candidate scan → flash verify (lesson-scoped source) on
    hits → one regen on confirmed drift. Returns
    (text, extra_prompt_tokens, extra_output_tokens). Fail-open: any problem
    keeps the original extract."""
    family = getattr(subjects.REGISTRY.get(subject), "family", "default")
    strict = family in _STRICT_FIDELITY_FAMILIES
    candidates = agent.extract_fidelity_candidates(out, book_text, strict=strict)
    if not candidates:
        return out, 0, 0                                   # no paid call
    source = await _verify_source_for_section(pdf_path, book_text, section)
    mismatches = await agent.verify_extract_fidelity(
        summary=out, book_text=source, candidates=candidates,
        provider=prov, model=mdl, transport=transport,
        homework_job_id=job_id, phase_output_id=po_id,
    )
    if not mismatches:
        return out, 0, 0
    logger.warning(f"[job {job_id}] extract fidelity: {len(mismatches)} drift(s) → regen: {mismatches}")
    corrected, tin2, tout2 = await agent.summarize_lesson(
        provider=prov, model=mdl, book_text=book_text,   # regen uses the SAME input the extract had
        section_title=section["title"], section_number=section["number"],
        page_start=section["page_start"], page_end=section["page_end"],
        homework_job_id=job_id, phase_output_id=po_id, transport=transport,
        correction_hint="\n".join(f"- {m}" for m in mismatches),
    )
    if agent.validate_extract_summary(corrected) is None:
        return corrected, tin2, tout2      # accept corrected
    return out, tin2, tout2                # regen refused → keep original, but bill the call


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
    transport: str = "cli",
    extract_transport: str = "cli",
    judge_transport: str = "cli",
    solver_transport: str = "cli",
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    solver_provider_ov: Optional[str] = None,
    solver_model_ov: Optional[str] = None,
    solver_boss_arena_enabled: bool = True,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
    output_language: str = "uz",
    lease: Optional[JobLease] = None,
) -> tuple[str, Optional[int], Optional[int], str, Optional[Any]]:
    _token = _token_of(lease)
    _custom_md = _custom_for(phase_name, custom_prompts)
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v4"
    elif _custom_md is not None:
        prompt_hash = "custom:sha256:" + hashlib.sha256(_custom_md.encode("utf-8")).hexdigest()
    else:
        prompt_hash = get_prompt_hash(subject, phase_name, output_language=output_language)

    # Per-phase model_name on the phase row records exactly what served this
    # call. The ``extract`` phase is pinned to the cheap-extractor settings
    # regardless of the job-level provider/model; every other phase honors
    # the user's pick.
    if phase_name == "extract":
        phase_model_label = extract_model
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
            lease=lease,
        )
        # A stale lease returns LeaseLost WITHOUT writing (not even the phase
        # row) — short-circuit before dereferencing po.id or writing `running`.
        if po is LeaseLost:
            raise LeaseLostSignal()
        po_id = po.id
        _pr = await phase_repo.set_status(
            session, po_id, "running", started_at=_utcnow(), claim_token=_token,
        )
        _jr = await jobs_repo.set_status(
            session, job_id, "running", current_phase=phase_name, claim_token=_token,
        )
        await session.commit()
        _raise_on_lease_signal(_pr)
        _raise_on_lease_signal(_jr)

    logger.debug(
        f"[job {job_id}] phase row created | phase={phase_name} order={phase_order} "
        f"prompt_hash={prompt_hash[:12]} provider={provider} model={phase_model_label}"
    )

    extract_warnings: list[str] = []
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
                        provider=extract_provider,
                        model=extract_model,
                    )

            if cached_extract is not None and cached_extract.output_md:
                logger.info(
                    f"[job {job_id}] lesson.extract REUSED from job={cached_extract.job_id} "
                    f"po={cached_extract.id} (skipping agent call)"
                )
                async with SessionLocal() as session:
                    _cr = await phase_repo.set_status(
                        session,
                        po_id,
                        "done",
                        completed_at=_utcnow(),
                        output_md=cached_extract.output_md,
                        tokens_input=0,
                        tokens_output=0,
                        # Same provenance as the non-cached extract path: a
                        # reused summary is still builtin-prompt markdown.
                        # Omitting this leaves the row NULL -> reads as
                        # `markdown_legacy`, which the contract reserves for
                        # pre-migration rows.
                        authoring_mode="markdown_builtin",
                        claim_token=_token,
                    )
                    await session.commit()
                _raise_on_lease_signal(_cr)
                # Visibility: record a free agent_usages row
                await agent.record_cached_lesson_extract(
                    homework_job_id=job_id,
                    phase_output_id=po_id,
                    source_job_id=cached_extract.job_id,
                    source_phase_output_id=cached_extract.id,
                )
                return cached_extract.output_md, 0, 0, prompt_hash, None

            # Pin lesson.extract to the cheap-extractor model regardless of the
            # job's per-phase provider/model: high-input/low-value factual summary.
            # Local whole-book text — no CLI file-read (dodges the gitignore block
            # + the >20MB ceiling). The model locates the lesson by title (R2-immune).
            book_text = await asyncio.to_thread(agent.read_whole_book_text, pdf_path)
            n_pages = await asyncio.to_thread(agent.pdf_page_count, pdf_path)
            was_oversize = agent.extract_text_is_oversize(book_text)
            if was_oversize:
                # Whole-book text exceeds the budget — scope to the lesson's pages as
                # TEXT (cheap, keeps transport=api), then run the normal path on the subset.
                ps, pe = section["page_start"], section["page_end"]
                if not ps or not pe:
                    raise RuntimeError(
                        "lesson.extract: book too large for whole-text extract and no "
                        "page range to scope a subset"
                    )
                book_text = await asyncio.to_thread(
                    agent.read_page_range_text, pdf_path, ps, pe,
                    margin=settings.extract_window_pages,
                )
                if agent.extract_text_is_oversize(book_text):
                    raise RuntimeError(
                        "lesson.extract: lesson page-subset still too large"
                    )
            # Scanned detection runs on the WHOLE-book text only (an oversize book is
            # dense by definition, so its subset is never 'scanned'): Gate A catches a
            # missing text layer; the density check catches a sparse header-only scan
            # that Gate A's absolute floor misses.
            gate_a = agent.validate_extract_text(book_text)
            scanned_reason = gate_a
            if scanned_reason is None and not was_oversize and agent.extract_text_is_too_sparse(book_text, n_pages):
                scanned_reason = (
                    f"sparse text layer ({len(book_text.strip()) // max(1, n_pages)} "
                    f"chars/page) — likely scanned"
                )
            if scanned_reason is not None:
                # Scanned / no-text-layer PDF: the whole-book text is unreadable, so
                # vision-attach a page-window of the lesson and let the model read it.
                # gemini+api attaches the window over Vertex; every other provider/
                # transport is forced to cli below (api PDF-attach is gemini-only).
                ps, pe = section["page_start"], section["page_end"]
                if not ps or not pe:
                    raise RuntimeError(
                        f"lesson.extract: {scanned_reason} and no page range to scope a vision extract"
                    )
                vision_transport = (
                    "api"
                    if (extract_provider == "gemini" and extract_transport == "api")
                    else "cli"
                )
                if extract_transport == "api" and vision_transport == "cli":
                    logger.info(
                        "lesson.extract: scanned PDF → forcing cli for vision "
                        "(only gemini api can attach); requested=api"
                    )
                out_md, tin, tout = await agent.summarize_lesson_vision(
                    provider=extract_provider, model=extract_model, pdf_path=pdf_path,
                    section_title=section["title"], section_number=section["number"],
                    page_start=ps, page_end=pe, homework_job_id=job_id, phase_output_id=po_id,
                    transport=vision_transport,
                )
                reason = agent.validate_extract_summary(out_md)
                if reason is not None:
                    raise failure_classifier.ExtractRefusal(
                        f"lesson.extract Gate B (vision): {reason}"
                    )
                output_md, produced_by = out_md, extract_provider
                parsed_struct = None
            else:
                async def _extract_run(prov: str, mdl: Optional[str]):
                    out, tin_, tout_ = await agent.summarize_lesson(
                        provider=prov, model=mdl, book_text=book_text,
                        section_title=section["title"], section_number=section["number"],
                        page_start=section["page_start"], page_end=section["page_end"],
                        homework_job_id=job_id, phase_output_id=po_id,
                        transport=extract_transport,
                    )
                    reason = agent.validate_extract_summary(out)
                    if reason is not None:
                        raise failure_classifier.ExtractRefusal(f"lesson.extract Gate B: {reason}")
                    out, xin, xout = await _verify_and_maybe_regen_extract(
                        out=out, book_text=book_text, pdf_path=pdf_path,
                        prov=prov, mdl=mdl, transport=extract_transport,
                        section=section, job_id=job_id, po_id=po_id, subject=subject,
                    )
                    return out, tin_ + xin, tout_ + xout

                output_md, tin, tout, produced_by = await _run_with_failover(
                    requested_provider=extract_provider,
                    model=extract_model,
                    run_fn=_extract_run,
                    transport=extract_transport,
                    session_limit_strategy=session_limit_strategy,
                )
                parsed_struct = None
            # Extract-completeness (warn-only): the only check that reads the
            # SOURCE instead of trusting the extract. Runs on the ACCEPTED
            # output — once per job, not once per failover attempt — and never
            # mutates it. The cross-job cache path returns above, so a reused
            # extract never re-pays.
            extract_warnings = await _check_extract_coverage(
                output_md=output_md, pdf_path=pdf_path, section=section,
                provider=extract_provider, model=extract_model,
                transport=extract_transport, job_id=job_id, po_id=po_id,
            )
            # extract is a builtin-prompt markdown phase — no structured lane.
            artifact = artifact_from_markdown(output_md, mode="markdown_builtin")
        else:
            base_phase_prompt = _custom_md if _custom_md is not None else get_prompt(subject, phase_name, output_language=output_language)
            # None for the 9 phases without a JSON-authoring prompt, and for any
            # phase whose contract the operator replaced with a custom upload.
            structured_prompt = (
                None
                if _custom_md is not None
                else get_structured_prompt(subject, phase_name, output_language=output_language)
            )

            def _make_run(prompt_text: str):
                async def _run(prov: str, mdl: Optional[str]):
                    return await agent.run_phase_prompt(
                        provider=prov,
                        model=mdl,
                        phase_prompt=prompt_text,
                        attachments=[pdf_path] if attach_file else [],
                        lesson_context=lesson_context or "",
                        prior_outputs=prior_outputs,
                        difficulty=difficulty,
                        phase_name=phase_name,
                        max_output_tokens=max_output_tokens_for(phase_name),
                        homework_job_id=job_id,
                        phase_output_id=po_id,
                        source_map_digest=source_map_digest,
                        transport=transport,
                    )
                return _run

            async def _generate(*, feedback: str, req_provider: str, req_model: Optional[str]):
                """One generation attempt as a whole artifact.

                Used by the initial generation AND by both regens, so a regen can
                never replace the markdown while leaving a stale content_json
                beside it — the artifact is always swapped wholesale.
                """
                return await _generate_artifact(
                    phase_name=phase_name,
                    is_custom=_custom_md is not None,
                    requested_provider=req_provider,
                    model=req_model,
                    run_fn=_make_run(base_phase_prompt + feedback),
                    structured_prompt=(
                        structured_prompt + feedback if structured_prompt else None
                    ),
                    transport=transport,
                    session_limit_strategy=session_limit_strategy,
                    lesson_context=lesson_context or "",
                    prior_outputs=prior_outputs,
                    difficulty=difficulty,
                    attachments=[pdf_path] if attach_file else [],
                    job_id=job_id,
                    po_id=po_id,
                    source_map_digest=source_map_digest,
                )

            artifact, tin, tout, produced_by = await _generate(
                feedback="", req_provider=provider, req_model=model,
            )
            output_md = artifact.output_md
            parsed_struct = None
    except (LeaseLostSignal, CancelWonSignal):
        raise  # control signal — never a phase-failed write (job is not ours)
    except SessionLimitPause:
        raise  # propagate to worker — phase must NOT be marked failed on a pause
    except Exception as exc:
        async with SessionLocal() as session:
            _fr = await phase_repo.set_status(
                session, po_id, "failed",
                completed_at=_utcnow(),
                error_message=_error_text(exc),
                claim_token=_token,
            )
            await session.commit()
        _raise_on_lease_signal(_fr)  # reclaim/cancel during the fail write → signal, not content failure
        raise

    warnings: list[str] = list(extract_warnings)
    judge_status: Optional[str] = None
    solver_status: Optional[str] = None
    if phase_name != "extract":
        # Judge against the phase's own contract, keyed off the ACTUAL producer
        # (produced_by + its resolved model). Capped regen loop (default 1 iter)
        # feeds cited failures back; budget exhausted → accept with warnings.
        # Never blocks the job.
        def _gen_model_of(prod: str) -> Optional[str]:
            # After failover, the fallback ran on model=None (provider default), so
            # tier selection uses the provider's DEFAULT model — errs toward a
            # stronger judge (safe per "judge >= generator"), not the CLI's exact
            # default. Approximate-but-safe; do not mistake it for exact.
            return model if prod == provider else None

        _jp, _jm = model_tiers.resolve_judge(
            produced_by, _gen_model_of(produced_by), judge_provider_ov, judge_model_ov,
        )
        outcome = await _judge_with_timeout(
            subject=subject, phase_name=phase_name, output_md=output_md,
            lesson_context=lesson_context, prior_outputs=prior_outputs,
            gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
            judge_provider=_jp, judge_model=_jm,
            homework_job_id=job_id, phase_output_id=po_id,
            transport=judge_transport,
            contract_override=_custom_md,
            output_language=output_language,
        )
        # Retry-once on unavailable: a transient CLI/parse failure (or timeout
        # degraded by C1's _judge_with_timeout) is worth one free retry.
        # Auth errors never reach here — phase_judge re-raises them before
        # degrading to unavailable — so retrying unavailable is always safe.
        # A content-policy refusal (outcome.refused) is recorded distinctly and
        # is NOT retried — it won't self-heal.
        if not outcome.available and not outcome.refused:
            logger.info(
                f"[job {job_id}] {phase_name} judge unavailable on first attempt — retrying once"
            )
            outcome = await _judge_with_timeout(
                subject=subject, phase_name=phase_name, output_md=output_md,
                lesson_context=lesson_context, prior_outputs=prior_outputs,
                gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
                judge_provider=_jp, judge_model=_jm,
                homework_job_id=job_id, phase_output_id=po_id,
                transport=judge_transport,
                output_language=output_language,
            )
        # Regenerate ONLY on a MAJOR issue; minor (stylistic/length) nits are
        # recorded as warnings but never trigger an expensive regen.
        # Loop is bounded by settings.max_judge_regens (default 1 → byte-identical
        # to the previous single-regen behavior).
        for _regen_attempt in range(settings.max_judge_regens):
            if not (outcome.available and outcome.has_major):
                break
            logger.info(
                f"[job {job_id}] {phase_name} judge found major issue(s) "
                f"({len(outcome.warnings)} total) — regenerating "
                f"(attempt {_regen_attempt + 1}/{settings.max_judge_regens}). "
                f"Issues: {outcome.warnings}"
            )
            # The regen runs through the failover driver, which CAN exhaust all
            # providers and raise. This block is OUTSIDE the generation try/except
            # (which marks the phase failed), so an unguarded raise here would fail
            # the whole job — violating "validation never fails a job". Guard it: on
            # regen failure keep the judge-rejected-but-complete original output +
            # its warnings and proceed to `done`.
            try:
                r_art, r_tin, r_tout, r_prod = await _generate(
                    feedback=outcome.feedback,
                    req_provider=produced_by,
                    req_model=_gen_model_of(produced_by),
                )
                # Commit to the regenerated output only after it actually succeeded.
                # The WHOLE artifact is swapped — a regen that falls back to
                # markdown carries content_json=None with it, so new markdown can
                # never be persisted beside the previous attempt's JSON.
                artifact, tin, tout, produced_by = r_art, r_tin, r_tout, r_prod
                output_md = artifact.output_md
                _jp2, _jm2 = model_tiers.resolve_judge(
                    produced_by, _gen_model_of(produced_by), judge_provider_ov, judge_model_ov,
                )
                outcome = await _judge_with_timeout(
                    subject=subject, phase_name=phase_name, output_md=output_md,
                    lesson_context=lesson_context, prior_outputs=prior_outputs,
                    gen_provider=produced_by, gen_model=_gen_model_of(produced_by),
                    judge_provider=_jp2, judge_model=_jm2,
                    homework_job_id=job_id, phase_output_id=po_id,
                    transport=judge_transport,
                    contract_override=_custom_md,
                    output_language=output_language,
                )
            except (LeaseLostSignal, CancelWonSignal):
                raise  # control signal — never degrade into the soft-keep path
            except SessionLimitPause:
                raise  # quota-pause during regen must propagate — not a content failure
            except Exception as exc:  # noqa: BLE001 — validation must NEVER fail a job (except api auth, below)
                if is_slot_saturation(exc):
                    raise SlotSaturation(str(exc)) from exc  # park, don't degrade
                if (transport == "api" or judge_transport == "api") and phase_judge._is_auth_error(exc):
                    # This try-block contains TWO spawns with potentially different
                    # transports: the regen GENERATION (content → `transport`) and
                    # the POST-REGEN JUDGE (→ `judge_transport`). An auth error here
                    # belongs to one of those two; if EITHER ran under api, the
                    # failure must be LOUD, consistent with the initial judge
                    # (spec §3) — don't silently degrade to the pre-regen output.
                    # Only pure cli+cli keeps the soft degrade.
                    logger.error(
                        f"[job {job_id}] {phase_name} api auth failure during regen/judge ({exc!r})"
                    )
                    raise
                logger.warning(
                    f"[job {job_id}] {phase_name} regen failed ({exc!r}); "
                    f"keeping the judge-rejected original output + warnings"
                )
                # output_md/tin/tout/produced_by and `outcome` retain their original
                # pre-regen values — the phase still completes `done` with warnings.
                judge_status = "major_regen_failed"
                break
        # Compute judge_status from the final outcome (or the soft-degrade above).
        # judge_status is None for non-judged phases (e.g. extract).
        if judge_status is None:
            if getattr(outcome, "refused", False):
                judge_status = "refused"
            elif not outcome.available:
                judge_status = "unavailable"
            elif outcome.passed or not outcome.has_major:
                judge_status = "ok"
            else:
                judge_status = "major_shipped"

        # CQ-C (R21.2): independent answer-key solver over the key-bearing phases.
        # Runs AFTER the judge so it checks the FINAL (possibly judge-regenerated)
        # output. Initial infrastructure unavailability remains advisory. Once a
        # HIGH-confidence mismatch is proven, however, this path is fail-closed:
        # only a regenerated artifact that the solver accepts may reach `done`.
        # The solver-regen output is adopted WITHOUT re-judging (accepted risk —
        # the solver only fixes the key).
        _solver_on = (
            settings.solver_enabled
            and phase_name in _SOLVER_PHASES
            and (phase_name != "boss-arena" or solver_boss_arena_enabled)
        )
        if _solver_on:
            _sp, _sm = model_tiers.resolve_solver(
                produced_by, _gen_model_of(produced_by), solver_provider_ov, solver_model_ov,
            )
            s_outcome = await solver.solve(
                subject=subject, phase_name=phase_name, phase_output_md=output_md,
                lesson_context=lesson_context, prior_outputs=prior_outputs,
                output_language=output_language,
                solver_provider=_sp, solver_model=_sm, transport=solver_transport,
                homework_job_id=job_id, phase_output_id=po_id, contract_override=_custom_md,
            )
            if not s_outcome.available:
                solver_status = "refused" if s_outcome.refused else "unavailable"
            elif not s_outcome.has_mismatch:
                solver_status = "ok"
            else:
                prior_mismatch_warnings = list(s_outcome.warnings)

                async def _block_solver(
                    mismatch_warnings: list[str],
                    repair_error: BaseException | None = None,
                ) -> NoReturn:
                    blocked = PersistentSolverMismatch(
                        phase_name, mismatch_warnings, repair_error
                    )
                    await _persist_solver_blocked_phase(
                        po_id=po_id,
                        artifact=artifact,
                        tin=tin,
                        tout=tout,
                        produced_by=produced_by,
                        warnings=(list(outcome.warnings) if outcome.available else [])
                        + mismatch_warnings,
                        judge_status=judge_status,
                        error=blocked,
                        claim_token=_token,
                    )
                    raise blocked

                for _s_regen in range(settings.max_solve_regens):
                    try:
                        r_art, r_tin, r_tout, r_prod = await _generate(
                            feedback=s_outcome.feedback,
                            req_provider=produced_by,
                            req_model=_gen_model_of(produced_by),
                        )
                        # Same whole-artifact swap as the judge regen above —
                        # markdown and content_json move together or not at all.
                        artifact, tin, tout, produced_by = r_art, r_tin, r_tout, r_prod
                        output_md = artifact.output_md
                        _sp2, _sm2 = model_tiers.resolve_solver(
                            produced_by, _gen_model_of(produced_by), solver_provider_ov, solver_model_ov,
                        )
                        s_outcome = await solver.solve(
                            subject=subject, phase_name=phase_name, phase_output_md=output_md,
                            lesson_context=lesson_context, prior_outputs=prior_outputs,
                            output_language=output_language,
                            solver_provider=_sp2, solver_model=_sm2, transport=solver_transport,
                            homework_job_id=job_id, phase_output_id=po_id, contract_override=_custom_md,
                        )
                        if s_outcome.available and not s_outcome.has_mismatch:
                            solver_status = "mismatch_regen"
                            break
                        if not s_outcome.available:
                            repair_error = s_outcome.failure or RuntimeError(
                                "solver recheck unavailable without an exception"
                            )
                            if isinstance(
                                repair_error,
                                (
                                    LeaseLostSignal,
                                    CancelWonSignal,
                                    SessionLimitPause,
                                    SlotSaturation,
                                    TransientPhaseError,
                                ),
                            ):
                                raise repair_error
                            if is_slot_saturation(repair_error):
                                raise SlotSaturation(str(repair_error)) from repair_error
                            if _requeue_worthy(repair_error):
                                raise repair_error
                            await _block_solver(
                                prior_mismatch_warnings, repair_error
                            )
                        prior_mismatch_warnings = list(s_outcome.warnings)
                    except PersistentSolverMismatch:
                        raise
                    except (
                        LeaseLostSignal,
                        CancelWonSignal,
                        SessionLimitPause,
                        SlotSaturation,
                        TransientPhaseError,
                    ):
                        raise
                    except Exception as exc:  # noqa: BLE001 — classify the repair failure
                        if is_slot_saturation(exc):
                            raise SlotSaturation(str(exc)) from exc
                        if _requeue_worthy(exc):
                            raise
                        await _block_solver(prior_mismatch_warnings, exc)
                else:
                    # Every allowed repair still has a solver-confirmed mismatch.
                    # Retain the final attempted artifact for inspection but never
                    # publish it as a completed phase.
                    final_mismatch_warnings = list(s_outcome.warnings)
                    await _block_solver(final_mismatch_warnings)

        # Infra states (unavailable/refused) carry ONLY the infra string — keep it
        # out of validation_warnings (content defects); judge_status records it and
        # the ExcType stays in the logs. major_shipped/major_regen_failed keep
        # available=True so their genuine content warnings survive.
        warnings = outcome.warnings if outcome.available else []
        # CQ-B (R21.3/R21.4): deterministic content lint. WARN-ONLY — findings
        # join validation_warnings under a `lint:` prefix, never gate a regen,
        # never fail a job. Pure function; defensively wrapped regardless.
        try:
            _lint = content_lint.lint_phase(
                phase_name, output_md, subject=subject, output_language=output_language,
            )
            warnings = warnings + content_lint.findings_to_warnings(_lint)
        except Exception as exc:  # noqa: BLE001 — lint must NEVER fail a job
            logger.warning(f"[job {job_id}] {phase_name} content_lint error ({exc!r}); skipping")
        if warnings:
            logger.warning(f"[job {job_id}] {phase_name} validation warnings: {warnings}")
    # The ONLY write of the generated content — after the judge regen AND the
    # solver regen. `artifact` is whatever survived them, so the markdown, the
    # JSON, the schema version and the renderer version are always the SAME
    # attempt's. Never add an earlier write here.
    async with SessionLocal() as session:
        _dr = await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md=output_md,
            tokens_input=tin,
            tokens_output=tout,
            validation_warnings=warnings or None,
            provider=produced_by,
            judge_status=judge_status,
            solver_status=solver_status,
            content_json=artifact.content_json,
            authoring_mode=artifact.authoring_mode,
            content_schema_version=artifact.content_schema_version,
            renderer_version=artifact.renderer_version,
            claim_token=_token,
        )
        await session.commit()
    # If the job was reclaimed while this phase ran, the fenced done-write
    # no-ops (LeaseLost) — surface the control signal so the pipeline unwinds
    # instead of reporting a completed phase for a job we no longer own.
    _raise_on_lease_signal(_dr)

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
