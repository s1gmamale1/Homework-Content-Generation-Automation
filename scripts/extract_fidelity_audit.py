"""Offline extract-fidelity audit CLI: grades already-generated `extract`
phase outputs against the textbook pages they were written from, to measure
how much factual drift occurs in language/humanities lessons (where the
deterministic CQ-D pre-filter is largely blind).

Usage:
  # Plan only — selects the sample, prints per-subject counts + an estimated
  # $, makes ZERO model calls. Always run this first.
  uv run python scripts/extract_fidelity_audit.py \
      --subject english --subject history:10 --subject geografiya:4 \
      --subject tarbiya:2 --subject adabiyot:2 --limit 48 --dry-run

  # Real run (bills transport=api gemini calls, hard-capped by --limit).
  uv run python scripts/extract_fidelity_audit.py \
      --subject english --subject history:10 --subject geografiya:4 \
      --subject tarbiya:2 --subject adabiyot:2 --limit 48

  # Calibration-only mode: reuse an already-paid-for report's pristine
  # results and run ONLY the mutated arm, on lessons pre-screened to
  # actually load and plant (fixes the "N picked before feasibility is
  # known" defect). --subject is not used in this mode.
  uv run python scripts/extract_fidelity_audit.py \
      --calibrate-from var/extract_fidelity_audit/task4-baseline.json \
      --mutations 8 --limit 16 --dry-run

`--subject NAME` takes ALL completed-extract lessons for that subject.
`--subject NAME:N` samples N of them (deterministic given `--sample-seed`,
best-effort stratified across (grade, source_language) cells — see
`extract_fidelity_audit.select_sample`). Repeat `--subject` for multiple
subjects.

`--limit` is a hard cap on BILLED MODEL CALLS, not on lessons: it is
enforced before every single logical call (pristine audit or mutated arm),
so the run stops mid-sample rather than overspending — never after the
fact. `--mutations N` picks N lessons (deterministically, from across the
WHOLE selected sample) to also run a paired sensitivity-calibration arm on;
those lessons' pristine result is REUSED (not re-run) so a mutation arm
costs exactly one extra call, not two.

`--calibrate-from <report.json>` switches to calibration-ONLY mode (Task
4b): candidates are that report's `reports[]` (already-audited pristine
lessons) instead of a fresh DB sample, `--subject` is unused, and
`efa.select_calibration_targets` screens each candidate for load+plant
feasibility BEFORE it is chosen as one of the `--mutations` targets — the
fix for a real run where 3/8 upfront-picked lessons failed to load and
2/8 had no plantable kind, so only 3 pairs were ever attempted. Exactly
one billed call per target (the mutated arm only); the pristine arm is
never re-run.

Read-only against production data: reads `homework_jobs` / `books` /
`phase_outputs` and `var/books/<id>/source.pdf`; writes nothing except the
`agent_usages` rows `agent.run_phase` itself records for this script's own
calls (tagged `operation="xfid:audit"`, `homework_job_id=None` — never
attributed to the production job whose extract is being graded) and the
JSON report file.

Cost note (carried from `teaching_audit._call`): the printed $ sums the
successful attempt of each logical call; a structured-output validation
retry logs an extra `agent_usages` row the printed total does not include,
so real spend may be marginally higher than what's printed here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.services import agent, extract_fidelity_audit as efa, pricing, storage  # noqa: E402

# Rough average $/call, derived from this instrument's own measured
# calibration run (48 gemini-3.5-flash calls ~= $0.85, see the plan doc) —
# NOT a substitute for the real `$` total printed after an actual run. Used
# only to give --dry-run a ballpark before spending anything.
_APPROX_COST_PER_CALL_USD = 0.018


def _parse_subject_arg(raw: str) -> tuple[str, Optional[int]]:
    """`"history"` -> (subject, None) meaning "take all"; `"history:10"` ->
    (subject, 10)."""
    name, sep, count = raw.partition(":")
    if not sep:
        return raw, None
    if not count.strip().isdigit():
        raise SystemExit(f"--subject {raw!r}: expected NAME or NAME:N (N an integer)")
    return name, int(count)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--subject", action="append", dest="subjects", default=None,
                   help="repeatable; NAME or NAME:N (N = sample size, default 'all available'). "
                        "Mutually exclusive with --calibrate-from.")
    p.add_argument("--calibrate-from", default=None,
                   help="path to a prior run's report.json; enables calibration-ONLY mode "
                        "(reuses that report's pristine reports[], runs only the mutated arm "
                        "on up to --mutations feasible targets). Mutually exclusive with --subject.")
    p.add_argument("--limit", type=int, required=True,
                   help="hard cap on BILLED MODEL CALLS (not lessons)")
    p.add_argument("--sample-seed", type=int, default=42)
    p.add_argument("--provider", default="gemini")
    p.add_argument("--model", default="gemini-3.5-flash")
    p.add_argument("--transport", default="api", help="transport=cli is retired operationally")
    p.add_argument("--mutations", type=int, default=8,
                   help="number of selected lessons (across the whole sample) to also "
                        "run a paired mutation-detection arm on; in --calibrate-from mode, "
                        "the number of feasible calibration targets to find")
    p.add_argument("--out", default=None, help="JSON report path (default var/extract_fidelity_audit/<stamp>.json)")
    p.add_argument("--dry-run", action="store_true",
                   help="select the sample and print the plan only — makes NO model call")
    args = p.parse_args(argv)
    if bool(args.subjects) == bool(args.calibrate_from):
        raise SystemExit(
            "exactly one of --subject (repeatable) or --calibrate-from <report.json> is required"
        )
    return args


async def _fetch_candidates(subjects: list[str]) -> dict[str, list[efa.LessonCandidate]]:
    """One DB round-trip: every (job, book) pair with a completed, non-empty
    `extract` phase output, for the requested subjects. Not unit-tested
    (needs a real DB) — mirrors why `load_extract_audit_inputs` itself is
    untested here; `efa.select_sample`, which consumes this function's
    output, IS the tested/pure part."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, PhaseOutput

    out: dict[str, list[efa.LessonCandidate]] = {s: [] for s in subjects}
    stmt = (
        select(
            HomeworkJob.id, HomeworkJob.subject, HomeworkJob.output_language,
            HomeworkJob.book_id, Book.grade, Book.source_language,
        )
        .join(Book, Book.id == HomeworkJob.book_id)
        .join(PhaseOutput, PhaseOutput.job_id == HomeworkJob.id)
        .where(
            HomeworkJob.subject.in_(subjects),
            PhaseOutput.phase_name == "extract",
            PhaseOutput.status == "done",
            PhaseOutput.output_md.isnot(None),
            PhaseOutput.output_md != "",
        )
    )
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    for job_id, subject, _output_language, book_id, grade, source_language in rows:
        out.setdefault(subject, []).append(
            efa.LessonCandidate(
                job_id=str(job_id), book_id=str(book_id), subject=subject,
                grade=grade, source_language=source_language,
            )
        )
    return out


def _plan(
    subjects: list[tuple[str, Optional[int]]],
    candidates_by_subject: dict[str, list[efa.LessonCandidate]],
    seed: int,
) -> dict[str, list[efa.LessonCandidate]]:
    """Deterministic per-subject sample selection (pure `efa.select_sample`
    underneath — this just loops subjects and prints nothing)."""
    selected: dict[str, list[efa.LessonCandidate]] = {}
    for subject, n in subjects:
        cands = candidates_by_subject.get(subject, [])
        selected[subject] = efa.select_sample(cands, n, seed, stratify=True)
    return selected


def _print_plan(
    subjects: list[tuple[str, Optional[int]]],
    candidates_by_subject: dict[str, list[efa.LessonCandidate]],
    selected: dict[str, list[efa.LessonCandidate]],
    mutation_targets: list[efa.LessonCandidate],
    limit: int,
) -> int:
    total = 0
    print("Sample plan (deterministic given --sample-seed):")
    for subject, _n in subjects:
        avail = len(candidates_by_subject.get(subject, []))
        chosen = selected[subject]
        cells = sorted({(c.grade, c.source_language) for c in chosen})
        total += len(chosen)
        print(f"  {subject}: {len(chosen)} of {avail} available "
              f"(cells sampled: {cells if cells else '[]'})")
    print(f"  TOTAL lessons: {total}")
    print(f"  mutation arms: {len(mutation_targets)} "
          f"(lessons: {[c.job_id[:8] for c in mutation_targets]})")
    planned_calls = total + len(mutation_targets)
    est_cost = planned_calls * _APPROX_COST_PER_CALL_USD
    print(f"  planned billed calls (ceiling, before any mutation-plant skips): {planned_calls}")
    print(f"  estimated cost: ~${est_cost:.2f} "
          f"(rough, ~${_APPROX_COST_PER_CALL_USD:.4f}/call — NOT the real total)")
    if planned_calls > limit:
        print(f"  NOTE: planned calls ({planned_calls}) exceed --limit ({limit}) — "
              f"the real run will stop early once the cap is hit.")
    return planned_calls


def _default_report_path() -> pathlib.Path:
    """The script's own default location — does NOT depend on `--out`, so
    it's a safe fallback when a caller-supplied `--out` path is unwritable
    (bad directory, permissions, full disk on that mount, ...)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return _REPO_ROOT / "var" / "extract_fidelity_audit" / f"{stamp}.json"


def _report_path(args: argparse.Namespace) -> pathlib.Path:
    return pathlib.Path(args.out) if args.out else _default_report_path()


def _write_report_to(out: pathlib.Path, payload: dict) -> pathlib.Path:
    """Write `payload` to `out` as JSON. `payload` must be JSON-serializable
    BY CONSTRUCTION (no Pydantic model / dataclass / `Path` / exception
    object reaching this call — every field is converted to a plain
    str/int/float/bool/list/dict/None where the payload is built) — this
    function deliberately does NOT pass a `default=` fallback handler to
    `json.dumps`, so a future field that forgets to convert fails LOUDLY
    here (caught by the caller's crash-safety wrapper) rather than being
    silently coerced into something that may not round-trip. Can raise
    (missing directory permissions, full disk, ...) — callers on the
    crash-safety path must catch this themselves; it is intentionally NOT
    swallowed here so a normal (non-crash) write failure still surfaces
    plainly."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")
    return out


def _cost(calls: list[dict]) -> float:
    return sum(
        pricing.cost_usd(c["provider"], c["model"], c["usage"])
        for c in calls
        if c.get("usage")
    )


def _target_subject_breakdown(targets: list) -> dict[str, int]:
    """Per-subject counts of chosen calibration targets, sorted by subject
    name for deterministic printing — the composition must be visible
    BEFORE spending (dry-run) and recorded in the real run's report, so a
    pool skewed toward one subject (e.g. all `english`) is caught before
    or right after a run, not discovered later by re-reading job_ids."""
    counts: dict[str, int] = {}
    for _pristine, inputs in targets:
        counts[inputs.subject] = counts.get(inputs.subject, 0) + 1
    return dict(sorted(counts.items()))


def _write_with_fallback(args: argparse.Namespace, payload: dict) -> Optional[pathlib.Path]:
    """Three-tier crash-safe write, shared by the full-audit run AND
    calibration-only mode: try the requested/default `--out` path first;
    on failure, print a loud stderr error and fall back to exactly ONE
    write at `_default_report_path()` (independent of any caller-supplied
    `--out`); if THAT also fails, dump the payload JSON straight to stderr
    as a last resort so billed results are at least recoverable from the
    terminal. NEVER raises — always returns the path actually written to,
    or `None` if all three tiers failed."""
    primary_path = _report_path(args)
    written_path: Optional[pathlib.Path] = None
    try:
        written_path = _write_report_to(primary_path, payload)
    except Exception as write_exc:
        print(
            f"ERROR: failed to write report to {primary_path}: "
            f"{type(write_exc).__name__}: {write_exc}",
            file=sys.stderr,
        )
        fallback_path = _default_report_path()
        try:
            written_path = _write_report_to(fallback_path, payload)
            print(f"Fell back to the default report path: {fallback_path}", file=sys.stderr)
        except Exception as fallback_exc:
            print(
                f"ERROR: fallback write to {fallback_path} ALSO failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc} — dumping the payload JSON "
                f"to stderr as a last resort so the billed results are not lost.",
                file=sys.stderr,
            )
            try:
                print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
            except Exception as dump_exc:  # pragma: no cover - last-resort guard
                print(
                    f"ERROR: could not even serialize the payload for a stderr dump: "
                    f"{type(dump_exc).__name__}: {dump_exc}",
                    file=sys.stderr,
                )
    return written_path


async def _run(args: argparse.Namespace) -> int:
    if args.calibrate_from:
        return await _run_calibrate_from(args)


    subjects = [_parse_subject_arg(s) for s in args.subjects]
    subject_names = [s for s, _n in subjects]

    candidates_by_subject = await _fetch_candidates(subject_names)
    selected = _plan(subjects, candidates_by_subject, args.sample_seed)
    all_selected = [c for _s, _n in subjects for c in selected[_s]]

    mutation_targets = efa.select_sample(
        all_selected, min(args.mutations, len(all_selected)), args.sample_seed, stratify=False
    )
    mutation_job_ids = {c.job_id for c in mutation_targets}

    planned_calls = _print_plan(subjects, candidates_by_subject, selected, mutation_targets, args.limit)

    if not all_selected:
        print("No candidate lessons found for the requested subjects — nothing to do.")
        return 0

    if args.dry_run:
        _write_report_to(_report_path(args), {
            "dry_run": True,
            "subjects": args.subjects,
            "sample_seed": args.sample_seed,
            "limit": args.limit,
            "mutations": args.mutations,
            "planned_lessons": len(all_selected),
            "planned_mutation_arms": len(mutation_targets),
            "planned_calls_ceiling": planned_calls,
            "estimated_cost_usd": planned_calls * _APPROX_COST_PER_CALL_USD,
            "sample": {s: [asdict(c) for c in selected[s]] for s, _n in subjects},
            "mutation_targets": [c.job_id for c in mutation_targets],
        })
        return 0

    calls: list[dict] = []
    reports: list[efa.ExtractFidelityReport] = []
    paired_results: list[efa.PairedFidelityResult] = []
    skipped_load_errors: list[dict] = []
    skipped_no_mutation: list[str] = []
    book_text_cache: dict[str, str] = {}
    stopped_early = False
    completed = False
    error_str: Optional[str] = None

    # Every iteration below can make a BILLED call (audit_one /
    # audit_with_control). If any of them raises — a dead adjudicator, an
    # exhausted-retries API error, a transient 5xx — the exception must
    # still surface (audit_one/audit_with_control stay fail-loud, unchanged)
    # but the results already paid for must NOT be thrown away: sample
    # selection is fully deterministic given --sample-seed, so the obvious
    # "just re-run the same command" recovery would silently re-bill every
    # lesson that already succeeded. try/finally makes report persistence
    # crash-safe: the `finally` block always runs, `completed`/`error_str`
    # record whether the run actually finished, and the exception is
    # re-raised unchanged after the report is written.
    try:
        for c in all_selected:
            if len(calls) >= args.limit:
                stopped_early = True
                break

            if c.book_id not in book_text_cache:
                pdf_path = storage.book_pdf_path(c.book_id)
                book_text_cache[c.book_id] = agent.read_whole_book_text(pdf_path)
            whole_text = book_text_cache[c.book_id]

            try:
                inputs = await efa.load_extract_audit_inputs(c.job_id, whole_book_text=whole_text)
            except efa.ExtractFidelityAuditError as exc:
                print(f"SKIP {c.job_id} ({c.subject}): {exc}")
                skipped_load_errors.append({"job_id": c.job_id, "subject": c.subject, "error": str(exc)})
                continue

            if len(calls) >= args.limit:
                stopped_early = True
                break
            report = await efa.audit_one(
                inputs, provider=args.provider, model=args.model, transport=args.transport, calls=calls
            )
            reports.append(report)
            print(f"[{len(reports)}/{len(all_selected)}] {c.subject} {c.job_id[:8]} "
                  f"ok={report.ok_count} contradicts={report.contradicts_count} "
                  f"unsupported={report.unsupported_count} (downgraded {report.downgraded_count})")

            if c.job_id in mutation_job_ids:
                if len(calls) >= args.limit:
                    stopped_early = True
                    break
                seed = efa.lesson_seed(args.sample_seed, c.job_id)
                paired = await efa.audit_with_control(
                    inputs, report, seed=seed, provider=args.provider, model=args.model,
                    transport=args.transport, calls=calls,
                )
                if paired is None:
                    skipped_no_mutation.append(c.job_id)
                    print(f"  mutation SKIP (no plantable kind): {c.job_id}")
                else:
                    paired_results.append(paired)
                    print(f"  mutation[{paired.kind}]: detected_planted={paired.detected_planted}")

        completed = True
    except Exception as exc:
        error_str = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # EVERYTHING in this block — payload construction, all three write
        # tiers, every diagnostic print — is wrapped in one outer
        # `try/except BaseException` (deliberately broader than `Exception`:
        # this is the one place where masking the original billed-call
        # error is worse than any alternative). Any statement here raising
        # while that error is in flight would otherwise REPLACE it (the
        # same bug class the three-tier write guard fixes for the write
        # itself, one level deeper — e.g. `summarize_runs`, a `model_dump()`
        # call, or even a `print(..., file=sys.stderr)` against a closed
        # stream). Nothing may propagate out of `finally`, ever.
        try:
            # Computed from whatever state exists at this point — correct
            # whether the run completed normally, was truncated by --limit,
            # or crashed mid-loop: a mutation-target lesson counts as "not
            # attempted" unless it produced a paired result or an explicit
            # no-plantable-kind skip. This is also how the calibration
            # denominator stays honest under a --limit truncation (not just
            # a crash) — Task 4's "≥6 of 8" gate needs to know if it was
            # really out of 8 attempts or fewer.
            attempted_or_skipped_mutation = {p.pristine.job_id for p in paired_results} | set(
                skipped_no_mutation
            )
            mutation_targets_not_attempted = [
                c.job_id for c in mutation_targets if c.job_id not in attempted_or_skipped_mutation
            ]

            summary = efa.summarize_runs(reports)
            detected = sum(1 for p in paired_results if p.detected_planted)
            cost = _cost(calls)

            if completed and stopped_early:
                print(f"STOPPED EARLY: --limit {args.limit} reached before the full sample was audited.")
            if completed:
                print(f"\ncalibration: {detected}/{len(paired_results)} planted mutations detected "
                      f"({len(skipped_no_mutation)} lessons had no plantable mutation, "
                      f"{len(mutation_targets_not_attempted)} not attempted)")
                print(f"cost: ${cost:.4f} across {len(calls)} logical calls "
                      f"({args.provider} {args.model}, {args.transport})")

            payload = {
                "dry_run": False,
                "completed": completed,
                "error": error_str,
                "subjects": args.subjects,
                "sample_seed": args.sample_seed,
                "limit": args.limit,
                "provider": args.provider,
                "model": args.model,
                "transport": args.transport,
                "stopped_early": stopped_early,
                "completed_job_ids": [r.job_id for r in reports],
                "reports": [r.model_dump() for r in reports],
                "paired_results": [
                    {
                        "kind": p.kind,
                        "mutation": {
                            "kind": p.mutation.kind, "original": p.mutation.original,
                            "replacement": p.mutation.replacement, "offset": p.mutation.offset,
                        },
                        "job_id": p.pristine.job_id,
                        "detected_planted": p.detected_planted,
                        "pristine": p.pristine.model_dump(),
                        "mutated": p.mutated.model_dump(),
                    }
                    for p in paired_results
                ],
                "calibration": {
                    "detected": detected, "total_pairs": len(paired_results),
                    "skipped_no_mutation": skipped_no_mutation,
                    "mutation_targets_not_attempted": mutation_targets_not_attempted,
                },
                "summary": summary,
                "skipped_load_errors": skipped_load_errors,
                "calls": [dict(c) for c in calls],
                "cost_usd": cost,
            }

            written_path = _write_with_fallback(args, payload)

            if not completed:
                where = f"wrote a PARTIAL report to {written_path}" if written_path is not None else (
                    "FAILED TO WRITE ANY REPORT FILE — see the stderr dump above"
                )
                print(
                    f"WARNING: run did NOT complete ({error_str}) — {where}, with "
                    f"{len(reports)} completed lesson(s) / ${cost:.4f} already billed across "
                    f"{len(calls)} call(s), so already-billed results are not lost. "
                    f"A manual partial re-run can use `completed_job_ids` to see what already ran.",
                    file=sys.stderr,
                )
        except BaseException as guard_exc:  # noqa: BLE001 - deliberate: see comment above
            try:
                print(
                    f"ERROR: extract-fidelity-audit report persistence itself failed "
                    f"({type(guard_exc).__name__}: {guard_exc}) — billed results may not "
                    f"have been saved; this is a bug in the report-writing path, not the "
                    f"audit itself.",
                    file=sys.stderr,
                )
            except BaseException:
                pass

    return 0


async def _run_calibrate_from(args: argparse.Namespace) -> int:
    """Task 4b: calibration-ONLY mode. Candidates are a prior run's
    already-audited pristine `reports[]` (loaded from `--calibrate-from`),
    never a fresh DB sample — `--subject` is unused in this mode.
    `efa.select_calibration_targets` screens each candidate for
    load+plant feasibility BEFORE it is chosen as one of the
    `--mutations` targets (the round-1 fix: selection used to happen
    before feasibility was known, so most upfront-picked lessons never
    produced a usable pair). For each target found, runs ONLY the mutated
    arm via `efa.audit_with_control` — exactly one billed call per pair,
    the pristine arm is reused from the loaded report and never re-run.

    Mirrors `_run`'s crash-safe `finally` persistence (own report file,
    same three-tier `_write_with_fallback`, same outer `except
    BaseException` guard) — a billed-call failure partway through must
    not discard results already paid for, same as the full-audit path.
    """
    report_path = pathlib.Path(args.calibrate_from)
    payload_in = json.loads(report_path.read_text(encoding="utf-8"))
    candidate_reports = [
        efa.ExtractFidelityReport.model_validate(r) for r in payload_in.get("reports", [])
    ]
    print(f"Calibration-only mode: {len(candidate_reports)} candidate lesson(s) loaded from {report_path}")

    targets, rejected_load, rejected_no_mutation = await efa.select_calibration_targets(
        candidate_reports, n=args.mutations, sample_seed=args.sample_seed,
    )

    examined = len(targets) + len(rejected_load) + len(rejected_no_mutation)
    print(f"targets found: {len(targets)}/{args.mutations} requested "
          f"(examined {examined}/{len(candidate_reports)} candidates: "
          f"{len(rejected_load)} rejected [load failure], "
          f"{len(rejected_no_mutation)} rejected [no plantable kind])")
    for rej in rejected_load:
        print(f"  REJECT load-failure {rej['job_id']} ({rej['subject']}): {rej['error']}")
    for job_id in rejected_no_mutation:
        print(f"  REJECT no-plantable-kind {job_id}")
    for _pristine, inputs in targets:
        print(f"  TARGET {inputs.job_id[:8]} ({inputs.subject})")

    subject_breakdown = _target_subject_breakdown(targets)
    print(f"  per-subject breakdown: {', '.join(f'{s}={n}' for s, n in subject_breakdown.items()) or '(none)'}")

    planned_calls = len(targets)
    est_cost = planned_calls * _APPROX_COST_PER_CALL_USD
    print(f"  planned billed calls: {planned_calls}")
    print(f"  estimated cost: ~${est_cost:.4f} "
          f"(rough, ~${_APPROX_COST_PER_CALL_USD:.4f}/call — NOT the real total)")
    if planned_calls > args.limit:
        print(f"  NOTE: planned calls ({planned_calls}) exceed --limit ({args.limit}) — "
              f"the real run will stop early once the cap is hit.")

    if args.dry_run:
        _write_report_to(_report_path(args), {
            "dry_run": True,
            "calibrate_from": str(report_path),
            "sample_seed": args.sample_seed,
            "limit": args.limit,
            "mutations_requested": args.mutations,
            "candidates_considered": len(candidate_reports),
            "targets_found": len(targets),
            "target_job_ids": [inputs.job_id for _p, inputs in targets],
            "target_subject_breakdown": subject_breakdown,
            "rejected_load": rejected_load,
            "rejected_no_mutation": rejected_no_mutation,
            "planned_calls_ceiling": planned_calls,
            "estimated_cost_usd": est_cost,
        })
        return 0

    if not targets:
        print("No viable calibration targets found — nothing to do.")
        return 0

    calls: list[dict] = []
    paired_results: list[efa.PairedFidelityResult] = []
    unexpected_no_plant: list[str] = []
    stopped_early = False
    completed = False
    error_str: Optional[str] = None

    # Same crash-safety contract as `_run`: a billed-call failure partway
    # through must not discard results already paid for.
    try:
        for pristine, inputs in targets:
            if len(calls) >= args.limit:
                stopped_early = True
                break
            seed = efa.lesson_seed(args.sample_seed, inputs.job_id)
            paired = await efa.audit_with_control(
                inputs, pristine, seed=seed, provider=args.provider, model=args.model,
                transport=args.transport, calls=calls,
            )
            if paired is None:
                # Defensive only — targets were pre-screened as plantable
                # with this exact (extract_md, seed), so this should not
                # happen. Logged and counted, never silently dropped.
                unexpected_no_plant.append(inputs.job_id)
                print(f"  mutation SKIP at run time (unexpected): {inputs.job_id}")
            else:
                paired_results.append(paired)
                print(f"  mutation[{paired.kind}] {inputs.job_id[:8]}: "
                      f"detected_planted={paired.detected_planted}")

        completed = True
    except Exception as exc:
        error_str = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            detected = sum(1 for p in paired_results if p.detected_planted)
            cost = _cost(calls)
            attempted = {p.pristine.job_id for p in paired_results} | set(unexpected_no_plant)
            targets_not_attempted = [
                inputs.job_id for _p, inputs in targets if inputs.job_id not in attempted
            ]

            if completed and stopped_early:
                print(f"STOPPED EARLY: --limit {args.limit} reached before all targets were audited.")
            if completed:
                print(f"\ncalibration: {detected}/{len(paired_results)} planted mutations detected "
                      f"({len(unexpected_no_plant)} unexpected no-plant at run time, "
                      f"{len(targets_not_attempted)} not attempted)")
                print(f"cost: ${cost:.4f} across {len(calls)} logical calls "
                      f"({args.provider} {args.model}, {args.transport})")

            payload = {
                "dry_run": False,
                "calibrate_from": str(report_path),
                "completed": completed,
                "error": error_str,
                "sample_seed": args.sample_seed,
                "limit": args.limit,
                "mutations_requested": args.mutations,
                "provider": args.provider,
                "model": args.model,
                "transport": args.transport,
                "stopped_early": stopped_early,
                "candidates_considered": len(candidate_reports),
                "targets_found": len(targets),
                "target_job_ids": [inputs.job_id for _p, inputs in targets],
                "target_subject_breakdown": subject_breakdown,
                "rejected_load": rejected_load,
                "rejected_no_mutation": rejected_no_mutation,
                "paired_results": [
                    {
                        "kind": p.kind,
                        "mutation": {
                            "kind": p.mutation.kind, "original": p.mutation.original,
                            "replacement": p.mutation.replacement, "offset": p.mutation.offset,
                        },
                        "job_id": p.pristine.job_id,
                        "detected_planted": p.detected_planted,
                        "pristine": p.pristine.model_dump(),
                        "mutated": p.mutated.model_dump(),
                    }
                    for p in paired_results
                ],
                "calibration": {
                    "detected": detected,
                    "total_pairs": len(paired_results),
                    "unexpected_no_plant_at_run": unexpected_no_plant,
                    "targets_not_attempted": targets_not_attempted,
                },
                "calls": [dict(c) for c in calls],
                "cost_usd": cost,
            }

            written_path = _write_with_fallback(args, payload)

            if not completed:
                where = f"wrote a PARTIAL report to {written_path}" if written_path is not None else (
                    "FAILED TO WRITE ANY REPORT FILE — see the stderr dump above"
                )
                print(
                    f"WARNING: calibration run did NOT complete ({error_str}) — {where}, with "
                    f"{len(paired_results)} completed pair(s) / ${cost:.4f} already billed across "
                    f"{len(calls)} call(s), so already-billed results are not lost.",
                    file=sys.stderr,
                )
        except BaseException as guard_exc:  # noqa: BLE001 - deliberate: see comment above `_run`
            try:
                print(
                    f"ERROR: extract-fidelity-audit calibration report persistence itself failed "
                    f"({type(guard_exc).__name__}: {guard_exc}) — billed results may not "
                    f"have been saved; this is a bug in the report-writing path, not the "
                    f"audit itself.",
                    file=sys.stderr,
                )
            except BaseException:
                pass

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except efa.ExtractFidelityAuditError as exc:
        raise SystemExit(f"extract-fidelity-audit failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
