from __future__ import annotations

import asyncio
import hashlib
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
from app.services import agent, book_fetch, events_bus, failure_classifier, model_tiers, notion_archive, phase_judge, storage
from app.services.agent_models import resolve_role_transport, resolve_session_limit_strategy
from app.services.errors import SessionLimitPause
from app.services.flows import (
    flow_for,
    file_needed_phases,
    filter_prior_outputs,
    max_output_tokens_for,
    resolve_phase_deps,
)
from app.services.prompts import get_prompt, get_prompt_hash

_INTERNAL_PHASES = {"extract", "classify"}


def _inject_grade(lesson_context: Optional[str], grade: Optional[str]) -> Optional[str]:
    """Prepend the student grade to the lesson context so the content-phase prompts'
    grade-band rules (deck size, reasoning load, distractor subtlety, question count)
    have a value to read — without this the grade never reached content generation.
    No-op when grade or lesson_context is missing."""
    if not grade or lesson_context is None:
        return lesson_context
    return f"Student grade level: {grade}\n\n{lesson_context}"


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


def _done_phase_md(rows) -> dict[str, str]:
    """Phase rows that are `done` with non-empty markdown — the resumable set."""
    return {
        r.phase_name: r.output_md
        for r in rows
        if r.status == "done" and (r.output_md or "").strip()
    }


def _resolve_extract(job_extract_provider, job_extract_model):
    """Extract role provider/model: explicit job override, else global settings.
    The settings default is the cheap pinned extractor (gemini-flash)."""
    return (
        job_extract_provider or settings.extract_provider,
        job_extract_model or settings.extract_model,
    )


def _pending_phases(content_phases: list[str], prior_outputs: dict[str, str]) -> set[str]:
    """Content phases still to run: everything not already in prior_outputs
    (done phases get pre-injected, so they're excluded and serve as deps)."""
    return {p for p in content_phases if p not in prior_outputs}


async def run(job_id: UUID) -> None:
    """Execute a homework job: extract → content phases → assemble."""
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
            # Per-job judge provider/model override (Task 6): explicit columns
            # let the user steer who grades; NULL falls back to the auto-tier
            # judge. Self-grade is still hard-swapped server-side downstream.
            judge_provider_ov = getattr(job, "judge_provider", None)
            judge_model_ov = getattr(job, "judge_model", None)
            # Per-job extract provider/model override (Task 4): explicit columns
            # win, else the cheap pinned extractor from settings. Content phases
            # are UNAFFECTED — they keep using job.provider / job.model.
            extract_provider, extract_model = _resolve_extract(
                getattr(job, "extract_provider", None),
                getattr(job, "extract_model", None),
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
            section_data = {
                "id": section.id,
                "title": section.section_title,
                "number": section.section_number,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "chapter": section.chapter_title or "",
            }

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
        full_flow = flow_for(subject)
        if selected_phases:
            chosen = set(selected_phases)
            content_planned = [p for p in full_flow if p in chosen]
        else:
            content_planned = full_flow
        sequence: list[str] = ["extract", *content_planned]
        log.info(f"[job {job_id}] sequence planned | phases={sequence}")

        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "running", started_at=_utcnow())
            await session.commit()

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
                    custom_prompts=custom_prompts,
                    judge_provider_ov=judge_provider_ov,
                    judge_model_ov=judge_model_ov,
                    extract_provider=extract_provider,
                    extract_model=extract_model,
                    session_limit_strategy=session_limit_strategy,
                )
            except SessionLimitPause:
                raise  # propagate to worker (Task 5) — job must NOT be marked failed
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
                    custom_prompts=custom_prompts,
                    judge_provider_ov=judge_provider_ov,
                    judge_model_ov=judge_model_ov,
                    extract_provider=extract_provider,
                    extract_model=extract_model,
                    session_limit_strategy=session_limit_strategy,
                )
            except SessionLimitPause:
                raise  # propagate to worker (Task 5) — must not be swallowed
            except RuntimeError as exc:
                if "content phase failed" in str(exc):
                    # _execute_one_phase already published the error and marked
                    # the job failed. Unwind cleanly without overwriting state.
                    return
                raise

        # No assembly — per-phase markdown in phase_outputs is the deliverable.
        async with SessionLocal() as session:
            await jobs_repo.set_status(session, job_id, "done", completed_at=_utcnow())
            await session.commit()

        await events_bus.publish(
            resource_id,
            "job_completed",
            {"job_id": str(job_id), "download_url": f"/api/v1/jobs/{job_id}/download"},
        )

        try:
            await notion_archive.archive_job(job_id)
        except Exception:
            log.warning(f"[job {job_id}] notion archive hook failed (non-fatal)", exc_info=True)

        total_s = perf_counter() - t_start
        log.success(
            f"[job {job_id}] pipeline complete | phases_run={len(sequence)} "
            f"total_s={total_s:.1f}"
        )
        await _log_token_summary(job_id, log)

    except SessionLimitPause:
        # Worker (Task 5) catches this and requeues with a cooldown — the job
        # must NOT be marked failed here.  Propagate after closing the SSE bus.
        raise
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
    transport: str = "cli",
    extract_transport: str = "cli",
    judge_transport: str = "cli",
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
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
            transport=transport,
            extract_transport=extract_transport,
            judge_transport=judge_transport,
            custom_prompts=custom_prompts,
            judge_provider_ov=judge_provider_ov,
            judge_model_ov=judge_model_ov,
            extract_provider=extract_provider,
            extract_model=extract_model,
            session_limit_strategy=session_limit_strategy,
        )
    except SessionLimitPause:
        raise  # worker requeues — job must NOT be marked failed
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
    source_map_ids: Optional[set[str]] = None,
    transport: str = "cli",
    extract_transport: str = "cli",
    judge_transport: str = "cli",
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
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
                            custom_prompts=custom_prompts,
                            judge_provider_ov=judge_provider_ov,
                            judge_model_ov=judge_model_ov,
                            extract_provider=extract_provider,
                            extract_model=extract_model,
                            session_limit_strategy=session_limit_strategy,
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
                except SessionLimitPause:
                    # Cancel in-flight peers, drain, then propagate so the worker
                    # can requeue with a cooldown.  Do NOT set failed=True — the
                    # job must NOT be marked failed on a pause.
                    for peer in in_flight.values():
                        peer.cancel()
                    if in_flight:
                        await asyncio.gather(*in_flight.values(), return_exceptions=True)
                        in_flight.clear()
                    raise
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
    except asyncio.CancelledError:
        # External cancel (user pressed Cancel). asyncio.wait() does NOT cancel
        # its awaitables, so we must cancel every in-flight phase and gather
        # them - that lets each _execute_phase -> _spawn run its
        # `except CancelledError: kill_tree(...)` before we unwind.
        for t in in_flight.values():
            t.cancel()
        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)
            in_flight.clear()
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
            except asyncio.TimeoutError as exc:
                # Attempt blew per_attempt_timeout — the provider is hung / too slow.
                # str(asyncio.TimeoutError()) == "" would misclassify as "hard"; and
                # retrying a hung provider is futile → fail over immediately (no
                # same-provider retry). Intercept BEFORE the classifier.
                last_exc = exc
                break
            except Exception as exc:  # noqa: BLE001 — classify, don't swallow
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
    custom_prompts: Optional[dict] = None,
    judge_provider_ov: Optional[str] = None,
    judge_model_ov: Optional[str] = None,
    extract_provider: Optional[str] = None,
    extract_model: Optional[str] = None,
    session_limit_strategy: str = "pause",
) -> tuple[str, Optional[int], Optional[int], str, Optional[Any]]:
    _custom_md = _custom_for(phase_name, custom_prompts)
    if phase_name == "extract":
        prompt_hash = "builtin:extract:v2"
    elif _custom_md is not None:
        prompt_hash = "custom:sha256:" + hashlib.sha256(_custom_md.encode("utf-8")).hexdigest()
    else:
        prompt_hash = get_prompt_hash(subject, phase_name)

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
                        provider=extract_provider,
                        model=extract_model,
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
                # Vision requires attachments → forced transport=cli (api is text-only).
                ps, pe = section["page_start"], section["page_end"]
                if not ps or not pe:
                    raise RuntimeError(
                        f"lesson.extract: {scanned_reason} and no page range to scope a vision extract"
                    )
                if extract_transport == "api":
                    logger.info(
                        "lesson.extract: scanned PDF → forcing transport=cli for vision; requested=api"
                    )
                out_md, tin, tout = await agent.summarize_lesson_vision(
                    provider=extract_provider, model=extract_model, pdf_path=pdf_path,
                    section_title=section["title"], section_number=section["number"],
                    page_start=ps, page_end=pe, homework_job_id=job_id, phase_output_id=po_id,
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
                    return out, tin_, tout_

                output_md, tin, tout, produced_by = await _run_with_failover(
                    requested_provider=extract_provider,
                    model=extract_model,
                    run_fn=_extract_run,
                    transport=extract_transport,
                    session_limit_strategy=session_limit_strategy,
                )
                parsed_struct = None
        else:
            base_phase_prompt = _custom_md if _custom_md is not None else get_prompt(subject, phase_name)

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

            output_md, tin, tout, produced_by = await _run_with_failover(
                requested_provider=provider, model=model,
                run_fn=_make_run(base_phase_prompt), transport=transport,
                session_limit_strategy=session_limit_strategy,
            )
            parsed_struct = None
    except SessionLimitPause:
        raise  # propagate to worker — phase must NOT be marked failed on a pause
    except Exception as exc:
        async with SessionLocal() as session:
            await phase_repo.set_status(
                session, po_id, "failed",
                completed_at=_utcnow(),
                error_message=str(exc),
            )
            await session.commit()
        raise

    warnings: list[str] = []
    judge_status: Optional[str] = None
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
                regen_prompt = base_phase_prompt + outcome.feedback
                r_md, r_tin, r_tout, r_prod = await _run_with_failover(
                    requested_provider=produced_by,
                    model=_gen_model_of(produced_by),
                    run_fn=_make_run(regen_prompt),
                    transport=transport,
                    session_limit_strategy=session_limit_strategy,
                )
                # Commit to the regenerated output only after it actually succeeded.
                output_md, tin, tout, produced_by = r_md, r_tin, r_tout, r_prod
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
                )
            except SessionLimitPause:
                raise  # quota-pause during regen must propagate — not a content failure
            except Exception as exc:  # noqa: BLE001 — validation must NEVER fail a job (except api auth, below)
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
        # Infra states (unavailable/refused) carry ONLY the infra string — keep it
        # out of validation_warnings (content defects); judge_status records it and
        # the ExcType stays in the logs. major_shipped/major_regen_failed keep
        # available=True so their genuine content warnings survive.
        warnings = outcome.warnings if outcome.available else []
        if warnings:
            logger.warning(f"[job {job_id}] {phase_name} validation warnings: {warnings}")
    async with SessionLocal() as session:
        await phase_repo.set_status(
            session, po_id, "done",
            completed_at=_utcnow(),
            output_md=output_md,
            tokens_input=tin,
            tokens_output=tout,
            validation_warnings=warnings or None,
            provider=produced_by,
            judge_status=judge_status,
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
