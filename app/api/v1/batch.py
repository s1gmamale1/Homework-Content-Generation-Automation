import asyncio
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import batches as batches_repo
from app.repositories import books as books_repo
from app.repositories import budget as budget_repo
from app.repositories import cost as cost_repo
from app.repositories import jobs as jobs_repo
from app.repositories import launch_defaults as launch_defaults_repo
from app.repositories import toc_entries as toc_repo
from app.services import subjects
from app.services.agent_models import (
    is_valid,
    resolve_output_language,
    resolve_output_language_for_book,
    resolve_role_selection,
    resolve_role_transport,
    resolve_role_transport_default,
    validate_output_language,
    validate_role_transport,
    validate_role_provider,
    validate_session_limit_strategy,
    validate_transport,
)
from app.services.flows import order_phase_selection, flow_for, selection_missing_prompts
from app.services import job_reactivation
from app.services.launch_stagger import stagger_offset
from app.services.toc_classifier import classify_entries, CLASSES

router = APIRouter(tags=["batches"])

log = logging.getLogger(__name__)

# Tracks in-flight head-side re-archive sweeps so a double-click can't launch a
# second concurrent sweep of the same batch (archive_job is idempotent, so this
# is an efficiency/politeness guard). Single-process (head: WORKER_CONCURRENCY=0);
# under --workers N the guard wouldn't span workers (harmless — idempotent).
_REARCHIVE_TASKS: dict[UUID, "asyncio.Task"] = {}


async def _rearchive_sweep(batch_id: UUID, job_ids: list[UUID], *, force: bool = False) -> None:
    """Sequentially re-run the idempotent, best-effort archive_job for each
    done-but-unarchived job in a batch, in the API process. Backgrounded; never
    raises (archive_job swallows + records skip reasons). Sequential because the
    Notion client is globally rate-limited. When `force`, each archive
    clears+rewrites stale leaf pages (replace mode)."""
    from app.services import notion_archive
    try:
        for jid in job_ids:
            try:
                await notion_archive.archive_job(jid, force=force)
            except Exception:  # defensive; archive_job is already best-effort
                log.warning("[batch %s] re-archive of job %s failed (non-fatal)",
                            batch_id, jid, exc_info=True)
    finally:
        _REARCHIVE_TASKS.pop(batch_id, None)


# Fleet batches default to the cli-first provider (master spec §1a); diverges
# from /generate's gemini default deliberately. model=None -> provider default.
_DEFAULT_PROVIDER = "claude"


class BatchLaunchRequest(BaseModel):
    book_id: UUID
    toc_entry_ids: Optional[list[UUID]] = None  # None = all lessons
    # Class filter applied only when toc_entry_ids is None: None defaults to
    # LESSON-only; an explicit list widens the set. Ignored (unfiltered) when
    # toc_entry_ids is set — an explicit pick always wins.
    include_classes: Optional[list[str]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    transport: str = "cli"
    extract_transport: str = "inherit"   # per-role override; "inherit" follows `transport`
    judge_transport: str = "inherit"
    solver_transport: str = "inherit"
    extract_provider: Optional[str] = None
    extract_model: Optional[str] = None
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    solver_provider: Optional[str] = None
    solver_model: Optional[str] = None
    output_language: str | None = None    # explicit pick; None → inherit global default
    force: bool = False
    preview: bool = False                 # compute disposition, don't mutate
    relaunch_mode: str = "resume"         # "resume" | "discard" for failed/cancelled-with-saved
    custom_prompts: dict[str, str] | None = None
    selected_phases: list[str] | None = None
    session_limit_strategy: str = "inherit"  # "pause" | "switch" | "inherit"
    # "homework" (default) | "teacher_material" — forks its own batch (Task 8)
    # and never resumes/adopts the other kind's job for the same section.
    kind: str = "homework"
    # ─── Per-request launch-stagger override (None = inherit the global) ────
    # `settings.batch_launch_wave_size`/`_interval_seconds` were sized against a
    # MEASURED incident on a 14-host fleet (per-job peak fan-out 5.54, so 6
    # jobs/wave ~= 33 calls against a credential ceiling of 32). That reasoning
    # still holds for THAT fleet — but the knobs are global and only settable at
    # process start, and a head restart re-stamps the fleet version floor (it can
    # fence every worker), so it is operationally reserved. On the 38-host fleet
    # 254 lessons at 6/60s means ~42 minutes before the last job is even
    # claimable: the ramp, not the credential cap, becomes the bottleneck and the
    # fleet can never saturate. These two fields let ONE launch pick its own ramp
    # without touching the head; nothing is persisted and the global default is
    # unchanged for every other launch.
    # 0 carries the same meaning as the settings kill switch: no stagger, every
    # job of this launch claimable immediately.
    wave_size: Optional[int] = Field(default=None, ge=0)
    wave_interval_seconds: Optional[int] = Field(default=None, ge=0)


def _stagger_summary(launched: int, wave_size: int, interval_seconds: int) -> dict:
    """What the launcher did to `scheduled_at`, so an operator can tell a
    deliberately staggered launch apart from a stuck queue. `waves` is 1 and the
    offset 0 when the stagger is off or the launch fits inside one wave."""
    last = stagger_offset(max(launched - 1, 0), wave_size=wave_size,
                          interval_seconds=interval_seconds)
    waves = (last // interval_seconds) + 1 if interval_seconds > 0 and last > 0 else 1
    return {"wave_size": wave_size, "interval_seconds": interval_seconds,
            "jobs_launched": launched, "waves": waves,
            "last_start_offset_seconds": last}


def _rollup_payload(batch, tally: dict[str, int], original_filename: str | None = None,
                    *, toc_total: int = 0, archived: int = 0, unarchived: int = 0,
                    stale: int = 0) -> dict:
    return {
        "batch_id": str(batch.id),
        "book_id": str(batch.book_id),
        "subject": batch.subject,
        "subject_variant": subjects.history_variant(batch.subject, original_filename),
        "grade": batch.grade,
        "kind": batch.kind,
        "output_language": batch.output_language,
        "provider": batch.provider,
        "model": batch.model,
        "transport": batch.transport,
        "extract_transport": batch.extract_transport,
        "judge_transport": batch.judge_transport,
        "solver_transport": batch.solver_transport,
        "extract_provider": batch.extract_provider,
        "extract_model": batch.extract_model,
        "judge_provider": batch.judge_provider,
        "judge_model": batch.judge_model,
        "solver_provider": batch.solver_provider,
        "solver_model": batch.solver_model,
        "rollup": tally,
        "lessons_covered": sum(tally.values()),
        "complete": sum(tally.values()) > 0 and tally.get("done", 0) == sum(tally.values()),
        "toc_total": toc_total,
        "created_at": batch.created_at.isoformat(),
        # Cost-safety fields (C4): None when the batch is not paused.
        "paused_at": batch.paused_at.isoformat() if batch.paused_at else None,
        "paused_reason": batch.paused_reason,
        "session_limit_strategy": batch.session_limit_strategy,
        "archived": archived,
        "unarchived": unarchived,
        "stale": stale,
    }


@router.post("/jobs/batch", status_code=201)
async def launch_batch(
    body: BatchLaunchRequest,
    session: AsyncSession = Depends(get_session),
):
    # Book-scoped SHARED advisory lock (BE-02 task 3) — taken right after
    # pydantic body validation, BEFORE the book fetch below, so that fetch
    # doubles as the post-lock re-read: a concurrent DELETE holding the
    # EXCLUSIVE form blocks us here until it commits/rolls back.
    await books_repo.lock_book_shared(session, body.book_id)
    book = await books_repo.get(session, body.book_id)
    if book is None:
        raise HTTPException(404, "book not found")
    if book.status in ("uploading", "toc_extracting"):
        raise HTTPException(409, "book still extracting — lessons available once TOC extraction completes")
    if book.status == "failed":
        raise HTTPException(409, f"book extraction failed: {book.error_message or 'unknown error'}")
    if book.status != "toc_ready":
        raise HTTPException(409, f"book not ready (status={book.status})")

    lessons = await toc_repo.list_for_book(session, body.book_id)
    if not lessons:
        raise HTTPException(422, "no lessons found for this book")

    if body.include_classes is not None:
        bad_classes = [c for c in body.include_classes if c not in CLASSES]
        if bad_classes:
            raise HTTPException(422, f"include_classes not recognized: {bad_classes}")

    by_id = {t.id: t for t in lessons}
    excluded_by_class: dict[str, int] = {}
    if body.toc_entry_ids is not None:
        bad = [tid for tid in body.toc_entry_ids if tid not in by_id]
        if bad:
            raise HTTPException(422, f"toc_entry_ids not in this book: {bad}")
        targets = [by_id[tid] for tid in body.toc_entry_ids]
    else:
        classes = classify_entries(lessons)
        wanted = set(body.include_classes) if body.include_classes is not None else {"lesson"}
        targets = [t for t, c in zip(lessons, classes) if c in wanted]
        for c in classes:
            if c not in wanted:
                excluded_by_class[c] = excluded_by_class.get(c, 0) + 1

    provider = body.provider or _DEFAULT_PROVIDER
    if not is_valid(provider, body.model):
        raise HTTPException(400, f"invalid provider/model: {provider}/{body.model}")

    transport_err = validate_transport(provider, body.model, body.transport)
    if transport_err is not None:
        raise HTTPException(400, transport_err)

    for field, value in (
        ("extract_transport", body.extract_transport),
        ("judge_transport", body.judge_transport),
        ("solver_transport", body.solver_transport),
    ):
        role_err = validate_role_transport(field, value)
        if role_err is not None:
            raise HTTPException(400, role_err)

    sls_err = validate_session_limit_strategy(body.session_limit_strategy)
    if sls_err is not None:
        raise HTTPException(400, sls_err)

    lang_err = validate_output_language(body.output_language, allow_none=True)
    if lang_err is not None:
        raise HTTPException(400, lang_err)

    if body.kind not in ("homework", "teacher_material"):
        raise HTTPException(400, f"invalid kind: {body.kind!r} (must be 'homework' or 'teacher_material')")

    # teacher_material is a fixed single-phase flow (`teacher-deck`) — custom
    # phase prompts / a phase pick would have nothing (or the wrong thing) to
    # apply to, and `flow_for(book.subject)` (the homework flow) is meaningless
    # for it. Reject outright rather than silently ignoring the fields; this
    # also means the flow_for-phase validation below never runs for a
    # teacher_material launch (custom_prompts/selected_phases stay None).
    if body.kind == "teacher_material" and (body.custom_prompts or body.selected_phases is not None):
        raise HTTPException(
            400,
            "custom_prompts/selected_phases are not supported for "
            "kind='teacher_material' (fixed single-phase teacher-deck flow)",
        )

    custom_prompts = body.custom_prompts or None
    if custom_prompts:
        valid_phases = set(flow_for(book.subject))
        for phase, md in custom_prompts.items():
            if phase == "extract" or phase not in valid_phases:
                raise HTTPException(400, f"custom_prompts: unknown phase {phase!r}")
            if len(md) > 20_000:
                raise HTTPException(
                    400, f"custom_prompts[{phase}] too long ({len(md)} chars; max 20000).")

    selected_phases = None
    if body.selected_phases is not None:
        try:
            selected_phases = order_phase_selection(book.subject, body.selected_phases)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # Pick-phases requires an uploaded prompt for every picked phase.
        missing = selection_missing_prompts(selected_phases, custom_prompts)
        if missing:
            raise HTTPException(
                400, f"selected phases missing a custom prompt: {missing}")

    batch_force = body.force or bool(custom_prompts) or selected_phases is not None
    # Per-role provider/model: validate only explicit picks. The role's effective
    # transport decides whether an explicit model is mandatory.
    for role, prov, mdl, role_tx in (
        ("extract", body.extract_provider, body.extract_model, body.extract_transport),
        ("judge", body.judge_provider, body.judge_model, body.judge_transport),
        ("solver", body.solver_provider, body.solver_model, body.solver_transport),
    ):
        if prov is None:
            continue
        if not is_valid(prov, mdl):
            raise HTTPException(400, f"{role}: unknown (provider, model) ({prov!r}, {mdl!r})")
        if role_err := validate_role_provider(role, prov):
            raise HTTPException(400, role_err)
        eff_tx = resolve_role_transport(role_tx, body.transport)
        err = validate_transport(prov, mdl, eff_tx)
        if err is not None:
            raise HTTPException(400, f"{role}: {err}")

    # Resolve roles against the UI-managed global defaults: explicit pick wins,
    # else the global default. Stamp CONCRETE provider/model onto job + batch so
    # jobs are self-describing (future-launches-only; agent_usages stays honest).
    ld = await launch_defaults_repo.get(session)
    res_judge_provider, res_judge_model = resolve_role_selection(
        body.judge_provider, body.judge_model, ld.judge_provider, ld.judge_model)
    res_extract_provider, res_extract_model = resolve_role_selection(
        body.extract_provider, body.extract_model, ld.extract_provider, ld.extract_model)
    res_solver_provider, res_solver_model = resolve_role_selection(
        body.solver_provider, body.solver_model, ld.solver_provider, ld.solver_model)
    res_judge_transport = resolve_role_transport_default(body.judge_transport, ld.judge_transport)
    res_extract_transport = resolve_role_transport_default(body.extract_transport, ld.extract_transport)
    res_solver_transport = resolve_role_transport_default(body.solver_transport, ld.solver_transport)
    res_output_language = resolve_output_language_for_book(
        body.output_language, book.source_language, ld.output_language)
    # Defense-in-depth: the resolved pairs must be manifest-valid (the global
    # default could only be off-manifest via a buggy PUT — fail loud, not silent).
    for role, prov, mdl in (("judge", res_judge_provider, res_judge_model),
                            ("extract", res_extract_provider, res_extract_model),
                            ("solver", res_solver_provider, res_solver_model)):
        if not is_valid(prov, mdl):
            raise HTTPException(500, f"{role}: resolved default off-manifest ({prov!r},{mdl!r})")
        if role_err := validate_role_provider(role, prov):
            raise HTTPException(400, f"{role} global default: {role_err}")
    # Gate: if the global default resolved a non-api-capable role provider to an
    # api effective transport, fail loud at launch rather than silently strand the
    # job unclaimable. cli-resolving transports always return None from
    # validate_transport and are never affected.
    for role, prov, mdl, res_tx in (
        ("judge", res_judge_provider, res_judge_model, res_judge_transport),
        ("extract", res_extract_provider, res_extract_model, res_extract_transport),
        ("solver", res_solver_provider, res_solver_model, res_solver_transport),
    ):
        eff_tx = resolve_role_transport(res_tx, body.transport)
        err = validate_transport(prov, mdl, eff_tx)
        if err is not None:
            raise HTTPException(400, f"{role} global default: {err}")
    # (a) STRICT zero-write preview — compute disposition and return BEFORE any
    # batch create/mutation. Leaves no phantom batch row in rollups.
    if body.preview:
        new = resumable = empty = retired = 0
        for t in targets:
            active = await jobs_repo.find_active_for_section(
                session, body.book_id, t.id, transport=body.transport,
                output_language=res_output_language, kind=body.kind)
            if active is not None:
                continue  # pending/running/done — not "remaining"
            latest = await jobs_repo.latest_for_section(
                session, body.book_id, t.id, transport=body.transport,
                output_language=res_output_language, kind=body.kind)
            if latest is not None and latest.status in ("failed", "cancelled"):
                # Disjoint from resumable/empty: a retired-stamped saved
                # section can never be safely resumed (it would reuse the
                # dead pinned model), regardless of how much saved work it has.
                if job_reactivation.retired_models_in_job(latest):
                    retired += 1
                elif await jobs_repo.done_phase_count_for_job(session, latest.id) > 0:
                    resumable += 1
                else:
                    empty += 1
            else:
                new += 1
        return JSONResponse(
            status_code=200,
            content={"book_id": str(body.book_id), "preview": True,
                     "new": new, "resumable": resumable, "empty": empty,
                     "retired": retired,
                     "target_count": len(targets),
                     "excluded_by_class": excluded_by_class})

    batch = await batches_repo.get_or_create_for_book(
        session, book_id=body.book_id, subject=book.subject, grade=book.grade,
        provider=provider, model=body.model, transport=body.transport,
        output_language=res_output_language,
        extract_transport=res_extract_transport,
        judge_transport=res_judge_transport,
        solver_transport=res_solver_transport,
        custom_prompts=custom_prompts, selected_phases=selected_phases,
        extract_provider=res_extract_provider,
        extract_model=res_extract_model,
        judge_provider=res_judge_provider,
        judge_model=res_judge_model,
        solver_provider=res_solver_provider,
        solver_model=res_solver_model,
        session_limit_strategy=body.session_limit_strategy,
        kind=body.kind)

    created = adopted = skipped = resumed = 0
    # Launch stagger (plan 2026-08-11). `launched` counts only the jobs THIS
    # call actually makes claimable — created + resumed. Adopted/skipped
    # sections are already running or already done: they add no load and must
    # not consume a wave slot, or a mostly-adopted relaunch of 8 new jobs would
    # be spread over 5 waves for nothing.
    # Resolve the ramp ONCE, here: an explicit per-request value wins, None
    # inherits the global setting, and the two fields resolve independently
    # (overriding only the size keeps the global interval). Everything below —
    # both offset call sites AND `_stagger_summary` in the response — reads these
    # locals, so the payload always reports what was ACTUALLY applied rather than
    # what the settings singleton happens to hold.
    _wave_size = (settings.batch_launch_wave_size if body.wave_size is None
                  else body.wave_size)
    _wave_interval = (settings.batch_launch_wave_interval_seconds
                      if body.wave_interval_seconds is None
                      else body.wave_interval_seconds)
    launched = 0
    # fleet-api-4: per-section rebill warnings (only populated on force path).
    # Format: [{toc_entry_id, prior_api_cost_usd, would_rebill}, ...]
    rebill_warnings: list[dict] = []

    for t in targets:
        await jobs_repo.lock_section_for_generate(session, body.book_id, t.id)
        # Transport-scoped lookup (spec §9a): an api batch over a cli-generated
        # book finds no same-transport job → falls through to create, leaving
        # the cli jobs untouched.
        existing = None if batch_force else await jobs_repo.find_active_for_section(
            session, body.book_id, t.id, transport=body.transport,
            output_language=res_output_language, kind=body.kind)
        if existing is not None:
            # Lookup is transport-scoped, so a returned job always matches —
            # guard it as belt-and-suspenders before adopting. A plain `assert`
            # would be stripped under `python -O`, exactly where the guard matters.
            if existing.transport != body.transport:
                raise RuntimeError(
                    f"find_active_for_section returned transport={existing.transport!r} "
                    f"for a transport={body.transport!r} lookup")
            if existing.batch_id is None:
                existing.batch_id = batch.id
                adopted += 1
            else:
                skipped += 1
            continue

        # fleet-api-4: force path — check for prior api spend before creating /
        # resetting the job so the operator sees what they're about to re-bill.
        if body.force:
            prior_cost, had_done_api_job = await cost_repo.section_prior_api_cost(
                session, body.book_id, t.id, body.transport)
            rebill_warnings.append({
                "toc_entry_id": str(t.id),
                "prior_api_cost_usd": prior_cost,
                "would_rebill": had_done_api_job and prior_cost > 0,
            })

        # No active (pending/running/done) job → "remaining". Resume a saved
        # failed/cancelled section instead of discarding it; else create fresh.
        latest = await jobs_repo.latest_for_section(
            session, body.book_id, t.id, transport=body.transport,
            output_language=res_output_language, kind=body.kind)
        if (latest is not None and latest.status in ("failed", "cancelled")
                and body.relaunch_mode != "discard"):
            # Resuming reuses the saved job's pinned provider/model verbatim —
            # refuse rather than silently reactivate a retired-stamped section
            # (would call a dead model) or silently fall through to a fresh
            # create (masks the operator's need to explicitly pick discard).
            retired = job_reactivation.retired_models_in_job(latest)
            if retired:
                raise HTTPException(
                    409,
                    detail={
                        "error": "retired_model",
                        "message": (
                            f"section {t.id} has saved work pinned to a "
                            "retired model; relaunch with "
                            "relaunch_mode='discard' to regenerate it with "
                            "a live model instead of resuming a dead one."
                        ),
                        "toc_entry_id": str(t.id),
                        "retired_roles": [
                            {"role": role, "provider": provider, "model": model}
                            for role, provider, model in retired
                        ],
                    },
                )
            await jobs_repo.reset_for_retry(
                session, latest.id, batch_id=batch.id,
                start_offset_seconds=stagger_offset(
                    launched, wave_size=_wave_size,
                    interval_seconds=_wave_interval))   # reuses done phases + adopts batch
            launched += 1
            resumed += 1
            continue
        # brand-new section, OR discard mode → fresh job (discard leaves the old
        # failed/cancelled row as history; find_active ignores it)
        await jobs_repo.create(session, book_id=body.book_id, toc_entry_id=t.id,
                               subject=book.subject, provider=provider,
                               model=body.model, batch_id=batch.id,
                               transport=body.transport,
                               output_language=res_output_language,
                               extract_transport=res_extract_transport,
                               judge_transport=res_judge_transport,
                               solver_transport=res_solver_transport,
                               custom_prompts=custom_prompts, selected_phases=selected_phases,
                               extract_provider=res_extract_provider,
                               extract_model=res_extract_model,
                               judge_provider=res_judge_provider,
                               judge_model=res_judge_model,
                               solver_provider=res_solver_provider,
                               solver_model=res_solver_model,
                               start_offset_seconds=stagger_offset(
                                   launched, wave_size=_wave_size,
                                   interval_seconds=_wave_interval),
                               kind=body.kind)
        launched += 1
        created += 1

    await session.flush()
    tally = await batches_repo.rollup_for_batch(session, batch.id)
    archive = await batches_repo.archive_rollup_for_batch(session, batch.id)
    toc_total = await batches_repo.toc_total_for_batch(session, batch.id)
    await session.commit()

    payload = _rollup_payload(batch, tally, book.original_filename, toc_total=toc_total,
                              archived=archive["archived"], unarchived=archive["unarchived"],
                              stale=archive["stale"])
    payload.update(jobs_created=created, jobs_adopted=adopted,
                   jobs_skipped=skipped, jobs_resumed=resumed,
                   rebill_warnings=rebill_warnings,
                   stagger=_stagger_summary(launched, _wave_size, _wave_interval))
    return payload


@router.get("/jobs/batches")
async def list_batches(session: AsyncSession = Depends(get_session)):
    rows = await batches_repo.list_with_rollups(session)
    return {"batches": [_rollup_payload(r["batch"], r["rollup"], r.get("original_filename"),
                                        toc_total=r["toc_total"],
                                        archived=r["archive"]["archived"],
                                        unarchived=r["archive"]["unarchived"],
                                        stale=r["archive"]["stale"])
                        for r in rows]}


@router.get("/jobs/batches/{batch_id}")
async def get_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    tally = await batches_repo.rollup_for_batch(session, batch_id)
    archive = await batches_repo.archive_rollup_for_batch(session, batch_id)
    toc_total = await batches_repo.toc_total_for_batch(session, batch_id)
    book = await books_repo.get(session, batch.book_id)
    return _rollup_payload(batch, tally, book.original_filename if book else None,
                           toc_total=toc_total,
                           archived=archive["archived"], unarchived=archive["unarchived"],
                           stale=archive["stale"])


@router.get("/jobs/batch/{batch_id}/cost")
async def get_batch_cost(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    """Return per-batch API spend + pause state + fleet pause state.

    Designed for operator observability: answers "what did this batch cost and
    why is it paused?"  The fleet `budget_state` singleton is included so the
    caller can distinguish a per-batch pause (budget cap reached on that batch)
    from a fleet-level pause (daily fleet cap reached).
    """
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    batch_cost = await cost_repo.batch_api_cost_usd(session, batch_id)
    fleet_state = await budget_repo.get_state(session)
    return {
        "batch_id": str(batch_id),
        "batch_api_cost_usd": batch_cost,
        "paused_at": batch.paused_at.isoformat() if batch.paused_at else None,
        "paused_reason": batch.paused_reason,
        "fleet_api_paused_at": (
            fleet_state.api_paused_at.isoformat() if fleet_state.api_paused_at else None
        ),
        "fleet_api_paused_reason": fleet_state.api_paused_reason,
    }


@router.post("/jobs/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    counts = await jobs_repo.cancel_all_in_batch(session, batch_id)
    running_ids = await jobs_repo.running_job_ids_in_batch(session, batch_id)
    await session.commit()
    # Instant local kill for any task running in THIS process (others self-cancel
    # via the heartbeat within heartbeat_seconds).
    from app.services.worker import RUNNING_JOBS
    for jid in running_ids:
        task = RUNNING_JOBS.get(jid)
        if task is not None:
            task.cancel()
    return {"batch_id": str(batch_id), **counts}


@router.post("/jobs/batch/{batch_id}/resume")
async def resume_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    book_id = batch.book_id
    # Book-scoped SHARED advisory lock (BE-02 task 3) — resume doesn't know
    # the book_id up front, so it derives it from the just-fetched batch
    # FIRST, then locks, then RE-FETCHES the batch: a concurrent DELETE
    # holding the EXCLUSIVE form blocks us here, and the re-fetch below sees
    # whatever remains once it releases (the batch/its jobs may have
    # vanished while we waited — never resurrect them).
    await books_repo.lock_book_shared(session, book_id)
    # `Session.get()` short-circuits via the identity map — expire the
    # pre-lock `batch` object first so this re-fetch actually re-queries
    # instead of silently returning the same stale in-memory row (see the
    # identical comment on jobs.py::retry_job; caught for real by
    # tests/integration/test_book_delete_race.py).
    session.expire(batch)
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    result = await jobs_repo.resume_failed_in_batch(
        session, batch_id,
        wave_size=settings.batch_launch_wave_size,
        interval_seconds=settings.batch_launch_wave_interval_seconds)
    await session.commit()
    return {"batch_id": str(batch_id), "jobs_resumed": result["resumed"],
            "jobs_skipped_retired": result["skipped_retired"],
            "stagger": _stagger_summary(
                result["resumed"], settings.batch_launch_wave_size,
                settings.batch_launch_wave_interval_seconds)}


@router.post("/jobs/batch/{batch_id}/pause")
async def pause_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    await batches_repo.pause_batch(session, batch_id, "manual")
    await session.commit()
    return {"batch_id": str(batch_id), "paused": True}


@router.post("/jobs/batch/{batch_id}/unpause")
async def unpause_batch(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    await batches_repo.unpause_batch(session, batch_id)
    await session.commit()
    return {"batch_id": str(batch_id), "paused": False}


@router.post("/jobs/batch/{batch_id}/retry-archive")
async def retry_archive_batch(batch_id: UUID, force: bool = False, stale: bool = False,
                              session: AsyncSession = Depends(get_session)):
    """Re-push every done-but-unarchived lesson of a batch to Notion from the
    HEAD process. With `force=true`, sweep ALL done lessons (incl. already
    archived) and clear+rewrite stale leaf pages — the regen-wave refresh lever.
    With `stale=true`, sweep ONLY the lessons whose page holds an older job's
    output (targeted refresh) with force. Backgrounded + idempotent; a second
    call while a sweep is in flight no-ops.

    Operational ordering: run force re-archive AFTER a regen wave has fully
    completed. The sweep takes the latest *done* job per lesson; if a replacement
    job is still running it isn't picked up, and once it later finishes its
    automatic archive skips-if-populated → the page goes stale again. Force once
    the wave is done."""
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    if batch_id in _REARCHIVE_TASKS:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": True}
    if stale:
        job_ids = await batches_repo.done_stale_job_ids(session, batch_id)
        sweep_force = True   # bypass the already-archived early-return + rewrite
    elif force:
        job_ids = await batches_repo.done_job_ids(session, batch_id)
        sweep_force = True
    else:
        job_ids = await batches_repo.done_unarchived_job_ids(session, batch_id)
        sweep_force = False
    if not job_ids:
        return {"batch_id": str(batch_id), "queued": 0, "already_running": False}
    _REARCHIVE_TASKS[batch_id] = asyncio.create_task(
        _rearchive_sweep(batch_id, job_ids, force=sweep_force))
    return {"batch_id": str(batch_id), "queued": len(job_ids), "already_running": False}


@router.get("/jobs/batches/{batch_id}/jobs")
async def list_batch_jobs(batch_id: UUID, session: AsyncSession = Depends(get_session)):
    from app.models.batch import Batch
    batch = await session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "batch not found")
    return {"batch_id": str(batch_id), "jobs": await batches_repo.list_jobs(session, batch_id)}
