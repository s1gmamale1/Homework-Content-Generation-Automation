"""CQ-E golden-eval CLI runner/gate.

Scores one golden-set job's generated packet against the 6-dimension rubric
(`app/services/golden_eval.py`), and gates a prompt/model-change PR on
no-regression vs a frozen baseline.

Usage:
    uv run python -m scripts.golden_eval --job <job_id> [--no-llm]
        [--baseline tests/golden/baselines/<job8>.json]
        [--emit-baseline tests/golden/baselines/<job8>.json]
        [--audit-check]

Flags:
    --job <id>            Golden-set job id (must be a `job_id` in
                           tests/golden/manifest.json).
    --no-llm              Deterministic-tier only (free) — skips boundary /
                           answer_key / extract_fidelity and the LLM half of
                           broken_question. Default runs the full paid tier.
    --baseline <file>      Diff the freshly-scored packet against a frozen
                           baseline JSON; exit 1 if `diff_scores` finds ANY
                           regression (a dim that was `pass` and is now
                           `flag`), else exit 0.
    --emit-baseline <file> Write the freshly-scored packet as the new
                           baseline JSON. **E4 guard**: refuses to write (and
                           exits non-zero) if ANY dimension's `detail`
                           contains "unavailable" — never freeze a
                           scorer-outage "pass" as ground truth.
    --audit-check          Compare the freshly-scored packet's verdicts
                           against the manifest's own `audit_verdict` (the
                           original human audit) and print got-vs-expected
                           per dimension. Minimal stub — Task 5 owns full
                           acceptance semantics/exit-code policy for this mode.

This script makes REAL model calls when `--no-llm` is not given (pay-per-token
`transport=api`). Never run it in a loop or against more than one job at a
time without operator sign-off (see CLAUDE.md "no homework-spam money rule").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from app.services import golden_eval as ge
from app.services import pricing

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SOURCES_DIR = _REPO_ROOT / "tests" / "golden" / "sources"


def _find_entry(job_id: str) -> ge.GoldenEntry:
    for entry in ge.load_golden_set():
        if entry.job_id == job_id:
            return entry
    raise SystemExit(f"job {job_id!r} not found in tests/golden/manifest.json")


def _load_source_text(job_id: str) -> str:
    path = _SOURCES_DIR / f"{job_id[:8]}.txt"
    if not path.exists():
        raise SystemExit(f"source fixture not found: {path}")
    return path.read_text(encoding="utf-8")


async def _next_lesson_title(book_id, toc_entry_id) -> str:
    """Look up the next TEACHING lesson's title after this job's TOC entry,
    via `toc_entries.get_next_in_book` (skips NULL-section end-matter rows).
    Returns "" when this is the last lesson in the book."""
    from app.db import SessionLocal
    from app.repositories import toc_entries as toc_repo

    async with SessionLocal() as session:
        current = await toc_repo.get(session, toc_entry_id)
        if current is None:
            return ""
        nxt = await toc_repo.get_next_in_book(session, book_id, current.order_index)
        return nxt.section_title if nxt is not None else ""


async def _job_book_and_toc_entry(job_id: str):
    """Read-only load of `(book_id, toc_entry_id)` for a job, straight off
    `homework_jobs` — needed to resolve the next-lesson title."""
    from app.db import SessionLocal
    from app.models import HomeworkJob

    async with SessionLocal() as session:
        job = await session.get(HomeworkJob, job_id)
        if job is None:
            raise SystemExit(f"homework_jobs row {job_id!r} not found")
        return job.book_id, job.toc_entry_id


def _total_llm_cost(score: ge.PacketScore, provider: str, model: str | None) -> float:
    """Sums `pricing.cost_usd` over every LLM-mechanism dimension's usage.
    Provider/model are the CLI's `--job`-scoped scorer args (same for every
    LLM call this run makes), not per-dimension — `DimensionScore.usage` is
    just the raw token-count dict, it doesn't carry provider/model."""
    total = 0.0
    for ds in score.scores.values():
        if ds.mechanism == "llm" and ds.usage:
            total += pricing.cost_usd(provider, model, ds.usage)
    return total


async def _run(args: argparse.Namespace) -> int:
    entry = _find_entry(args.job)
    phases = await ge._load_phases_from_db(args.job)
    source_text = _load_source_text(args.job)
    book_id, toc_entry_id = await _job_book_and_toc_entry(args.job)
    next_lesson_title = await _next_lesson_title(book_id, toc_entry_id)

    score = await ge.score_packet(
        entry, phases, source_text, next_lesson_title,
        provider=args.provider, model=args.model, transport="api",
        llm=not args.no_llm,
    )

    print(f"=== golden-eval report: job={score.job_id} ===")
    for dim in ge._DIMENSIONS:
        ds = score.scores.get(dim)
        if ds is None:
            print(f"  {dim:16s}  (omitted — no-llm run)")
            continue
        print(f"  {dim:16s}  {ds.verdict:5s}  [{ds.mechanism}]  {ds.detail}")
    total_cost = _total_llm_cost(score, args.provider, args.model)
    print(f"--- total LLM cost: ${total_cost:.4f} ---")

    exit_code = 0

    if args.emit_baseline:
        unavailable = [
            dim for dim, ds in score.scores.items() if "unavailable" in ds.detail.lower()
        ]
        if unavailable:
            print(
                f"ERROR: refusing to emit baseline — scorer-unavailable degrade-to-pass "
                f"on dimension(s) {unavailable}; re-run once the scorer is healthy.",
                file=sys.stderr,
            )
            return 1
        out_path = pathlib.Path(args.emit_baseline)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(ge.packet_score_to_dict(score), indent=2), encoding="utf-8")
        print(f"baseline written: {out_path}")

    if args.baseline:
        baseline_path = pathlib.Path(args.baseline)
        if not baseline_path.exists():
            raise SystemExit(f"baseline file not found: {baseline_path}")
        baseline = ge.packet_score_from_dict(json.loads(baseline_path.read_text(encoding="utf-8")))
        regressions = ge.diff_scores(baseline, score)
        if regressions:
            print("REGRESSIONS DETECTED:")
            for r in regressions:
                print(f"  - {r}")
            exit_code = 1
        else:
            print("no regressions vs baseline.")

    if args.audit_check:
        # Minimal stub — Task 5 owns full acceptance semantics for this mode.
        print("--- audit-check (got vs expected audit_verdict) ---")
        for dim, expected in entry.audit_verdict.items():
            got = score.scores.get(dim)
            got_verdict = got.verdict if got is not None else "(omitted)"
            match = "OK" if got_verdict == expected else "MISMATCH"
            print(f"  {dim:16s}  expected={expected:5s}  got={got_verdict:9s}  {match}")

    return exit_code


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job", required=True, help="golden-set job id")
    p.add_argument("--no-llm", action="store_true", help="deterministic-tier only (free)")
    p.add_argument("--baseline", default=None, help="baseline JSON to diff against; exits 1 on regression")
    p.add_argument("--emit-baseline", default=None, help="write the score as the new baseline JSON")
    p.add_argument("--audit-check", action="store_true", help="compare vs manifest audit_verdict (stub)")
    p.add_argument("--provider", default="gemini", help="LLM-scorer provider (default: gemini)")
    p.add_argument("--model", default="gemini-2.5-pro", help="LLM-scorer model (default: gemini-2.5-pro)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
