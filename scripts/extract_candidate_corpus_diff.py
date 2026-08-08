"""Corpus regression proof for the extract-fidelity family gate (Task 6).

Read-only, `$0`, ZERO model calls. For every `done` extract phase output in
the database, computes the GROUNDED production candidate list — i.e. the
exact call the pipeline makes, `agent.extract_fidelity_candidates(md,
whole_book_text, strict=...)` — both without the strict post-filter and with
it, and prints a per-subject before/after table.

Why "grounded, not raw" matters: `agent.extract_math_expressions(summary)`
alone (no book-text grounding) is only an UPPER BOUND — it also matches
expressions that genuinely appear in the source book, which the fidelity
guard never flags because they aren't drift candidates. An earlier draft of
the plan this harness supports quoted raw numbers as if they were production
behavior; that overstated humanities guard activity by 2-5x. This script
exists to make that mistake impossible to repeat: it only ever calls
`agent.extract_fidelity_candidates`, never `agent.extract_math_expressions`
directly.

THE STOP-GATE IS A WITHIN-RUN INVARIANT, NOT A FROZEN CONSTANT. The corpus is
live (new jobs land daily), so a frozen expected-count assertion would be
wrong by construction. Instead:

  - HARD STOP (must hold for a correct implementation, immune to corpus
    growth): for every `math`/`sciences`-family subject, the PRODUCTION
    candidate list (`after` — the exact call the pipeline makes, gated by
    `family in pipeline._STRICT_FIDELITY_FAMILIES`) must equal the
    strict-off candidate list (`before`), lesson-by-lesson, within this same
    run. Since `_STRICT_FIDELITY_FAMILIES` is `{"languages", "humanities"}`,
    `after` equals `before` by construction for every math/sciences subject —
    so this check is really a live assertion on the *imported* frozenset
    itself: it can only fail if `_STRICT_FIDELITY_FAMILIES` is ever
    misconfigured to include `math` or `sciences` (a real leak), because
    that is the only way `after` diverges from `before` for those families.
    An earlier draft of this check instead compared `before` against an
    UNCONDITIONAL `strict=True` pass (`under_strict`) — that comparison is
    meaningless: production never calls
    `extract_fidelity_candidates(strict=True)` for math/sciences, so
    `under_strict` is a counterfactual that never occurs in production, and
    forcing it on trivially drops digit/`=`-bearing math tokens (e.g.
    `(p/p`), reporting a false "violation" against a perfectly correct
    implementation. Exits non-zero on violation.
  - INFORMATIONAL ONLY (never a gate): a delta vs. the 2026-08-07 snapshot
    recorded in the Task 6 plan. Corpus growth moves these numbers
    legitimately; a difference here is expected drift, not failure.

Usage:
  export DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5432/edu_copy"
  export VAR_DIR="/path/to/var"
  uv run python scripts/extract_candidate_corpus_diff.py

  # Debug-only knob, never used for the real proof run: cap how many books'
  # worth of extracts are processed, for a fast local smoke of this script.
  uv run python scripts/extract_candidate_corpus_diff.py --max-books 3
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Optional

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.services import agent, storage, subjects  # noqa: E402
from app.services.pipeline import _STRICT_FIDELITY_FAMILIES  # noqa: E402

# ─── 2026-08-07 informational snapshot (Task 6 brief) — NOT a gate ─────────
# subject -> (lessons_before, lessons_after, exprs_before, exprs_after)
_SNAPSHOT: dict[str, tuple[int, int, int, int]] = {
    "english": (8, 0, 26, 0),
    "history": (19, 6, 34, 7),
    "geografiya": (9, 2, 10, 2),
    "tarbiya": (0, 0, 0, 0),
    "adabiyot": (0, 0, 0, 0),
    "biology": (18, 18, 23, 23),
    "physics": (66, 66, 127, 127),
    "kimyo-g7-11": (25, 25, 56, 56),
    "math-algebra": (210, 210, 715, 715),
    "matematika": (21, 21, 44, 44),
    "geometriya-g7-11": (69, 69, 153, 153),
}
_SNAPSHOT_TOTAL_EXTRACTS = 3427

_SAMPLE_LIMIT = 8  # how many dropped/kept token examples to print per subject


@dataclass
class LessonRow:
    job_id: str
    subject: str
    book_id: str
    output_md: str


@dataclass
class SubjectAgg:
    lessons_total: int = 0
    lessons_before: int = 0
    lessons_after: int = 0
    exprs_before: int = 0
    exprs_after: int = 0
    dropped_samples: list[str] = field(default_factory=list)
    kept_samples: list[str] = field(default_factory=list)
    # Only populated for math/sciences families — the within-run invariant.
    invariant_violations: list[tuple[str, list[str], list[str]]] = field(default_factory=list)


async def _fetch_all_done_extracts() -> list[LessonRow]:
    """One DB round-trip: every (job, book, extract markdown) triple for a
    `done` extract phase output, across ALL subjects. Ordered by book_id so
    the caller can cache whole-book text with a single linear pass."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, PhaseOutput

    stmt = (
        select(
            HomeworkJob.id, HomeworkJob.subject, HomeworkJob.book_id, PhaseOutput.output_md,
        )
        .join(Book, Book.id == HomeworkJob.book_id)
        .join(PhaseOutput, PhaseOutput.job_id == HomeworkJob.id)
        .where(
            PhaseOutput.phase_name == "extract",
            PhaseOutput.status == "done",
            PhaseOutput.output_md.isnot(None),
            PhaseOutput.output_md != "",
        )
        .order_by(HomeworkJob.book_id, HomeworkJob.subject, HomeworkJob.id)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    return [
        LessonRow(job_id=str(job_id), subject=subject, book_id=str(book_id), output_md=output_md)
        for job_id, subject, book_id, output_md in rows
    ]


def _family_for(subject: str) -> str:
    """Mirrors pipeline._verify_and_maybe_regen_extract's exact lookup so the
    harness can never silently diverge from production's family resolution."""
    return getattr(subjects.REGISTRY.get(subject), "family", "default")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--max-books", type=int, default=None,
        help="DEBUG ONLY: cap the number of distinct book_ids processed, for a fast local "
             "smoke of this script. Never use this for the real proof run — the invariant "
             "and snapshot delta are only meaningful over the full corpus.",
    )
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    print("Fetching every `done` extract phase output (read-only)...")
    rows = await _fetch_all_done_extracts()
    print(f"  {len(rows)} extracts fetched (2026-08-07 snapshot total: {_SNAPSHOT_TOTAL_EXTRACTS})")

    book_text_cache: dict[str, Optional[str]] = {}
    skipped_pdf_errors: list[tuple[str, str]] = []
    aggs: dict[str, SubjectAgg] = {}
    books_seen: list[str] = []

    for row in rows:
        if row.book_id not in book_text_cache:
            if args.max_books is not None and len(books_seen) >= args.max_books:
                continue
            books_seen.append(row.book_id)
            try:
                pdf_path = storage.book_pdf_path(row.book_id)
                book_text_cache[row.book_id] = agent.read_whole_book_text(pdf_path)
            except Exception as exc:  # noqa: BLE001 - read-only best-effort corpus scan
                book_text_cache[row.book_id] = None
                skipped_pdf_errors.append((row.book_id, f"{type(exc).__name__}: {exc}"))
                print(f"  SKIP book {row.book_id}: {type(exc).__name__}: {exc}")

        whole_text = book_text_cache.get(row.book_id)
        if whole_text is None:
            continue

        # THE production call, unmodified — grounded, not raw.
        before = agent.extract_fidelity_candidates(row.output_md, whole_text, strict=False)
        # `under_strict` is only meaningful — and only used below — as the
        # `after` value for strict-family (languages/humanities) subjects,
        # matching what production actually invokes for them. For
        # math/sciences it is deliberately unused: production never sets
        # strict=True there, so comparing against it would assert a
        # counterfactual (see the module docstring's HARD STOP section).
        under_strict = agent.extract_fidelity_candidates(row.output_md, whole_text, strict=True)

        family = _family_for(row.subject)
        strict_production = family in _STRICT_FIDELITY_FAMILIES
        after = under_strict if strict_production else before

        agg = aggs.setdefault(row.subject, SubjectAgg())
        agg.lessons_total += 1
        if before:
            agg.lessons_before += 1
        if after:
            agg.lessons_after += 1
        agg.exprs_before += len(before)
        agg.exprs_after += len(after)

        if strict_production:
            dropped = [c for c in before if c not in after]
            kept = [c for c in after]
            if dropped and len(agg.dropped_samples) < _SAMPLE_LIMIT:
                agg.dropped_samples.extend(dropped[: _SAMPLE_LIMIT - len(agg.dropped_samples)])
            if kept and len(agg.kept_samples) < _SAMPLE_LIMIT:
                agg.kept_samples.extend(kept[: _SAMPLE_LIMIT - len(agg.kept_samples)])

        if family in ("math", "sciences") and before != after:
            agg.invariant_violations.append((row.job_id, before, after))

    # ── HARD STOP: within-run math/sciences invariant ──────────────────────
    print("\n" + "=" * 78)
    print("HARD STOP — within-run math/sciences invariant "
          "(production list must equal before-strict list)")
    print("=" * 78)
    violated_subjects = [s for s, a in aggs.items() if a.invariant_violations]
    checked_subjects = [
        s for s in aggs if _family_for(s) in ("math", "sciences")
    ]
    for s in sorted(checked_subjects):
        agg = aggs[s]
        status = "VIOLATED" if agg.invariant_violations else "held"
        print(f"  {s} (family={_family_for(s)}): {status} "
              f"({len(agg.invariant_violations)} violation(s) / {agg.lessons_total} lessons)")
        for job_id, before, after in agg.invariant_violations[:5]:
            print(f"    job {job_id}: before={before} after={after}")

    invariant_held = not violated_subjects
    if invariant_held:
        print(f"\nINVARIANT HELD across {len(checked_subjects)} math/sciences subject(s).")
    else:
        print(f"\nINVARIANT VIOLATED for: {sorted(violated_subjects)} — "
              f"the strict post-filter has leaked into the math/sciences path. STOP.")

    # ── Per-subject before/after production table ──────────────────────────
    print("\n" + "=" * 78)
    print("PER-SUBJECT PRODUCTION BEFORE/AFTER "
          "(before = strict=False always; after = actual production call)")
    print("=" * 78)
    print(f"{'subject':<22}{'family':<12}{'lessons':<10}"
          f"{'lessons_before':<16}{'lessons_after':<15}{'exprs_before':<14}{'exprs_after':<12}")
    for s in sorted(aggs):
        agg = aggs[s]
        family = _family_for(s)
        print(f"{s:<22}{family:<12}{agg.lessons_total:<10}"
              f"{agg.lessons_before:<16}{agg.lessons_after:<15}{agg.exprs_before:<14}{agg.exprs_after:<12}")

    # ── Sample dropped vs kept tokens (strict families only) ───────────────
    print("\n" + "=" * 78)
    print("SAMPLE DROPPED (glosses) vs KEPT (digit/= bearing) — strict families only")
    print("=" * 78)
    for s in sorted(aggs):
        if _family_for(s) not in _STRICT_FIDELITY_FAMILIES:
            continue
        agg = aggs[s]
        print(f"  {s}:")
        print(f"    dropped: {agg.dropped_samples or '(none)'}")
        print(f"    kept:    {agg.kept_samples or '(none)'}")

    # ── Informational snapshot delta — NEVER a gate ─────────────────────────
    print("\n" + "=" * 78)
    print("INFORMATIONAL ONLY — delta vs 2026-08-07 snapshot (NOT a gate; corpus is live)")
    print("=" * 78)
    for s in sorted(set(_SNAPSHOT) | set(aggs)):
        snap = _SNAPSHOT.get(s)
        agg = aggs.get(s)
        if agg is None:
            print(f"  {s}: NO DATA in this run (snapshot had {snap})")
            continue
        cur = (agg.lessons_before, agg.lessons_after, agg.exprs_before, agg.exprs_after)
        if snap is None:
            print(f"  {s}: not in snapshot; current lessons {cur[0]}->{cur[1]} exprs {cur[2]}->{cur[3]}")
            continue
        same = "==" if cur == snap else "DRIFTED"
        print(f"  {s}: snapshot lessons {snap[0]}->{snap[1]} exprs {snap[2]}->{snap[3]}  "
              f"| now lessons {cur[0]}->{cur[1]} exprs {cur[2]}->{cur[3]}  [{same}]")
    print(f"\n  total extracts: snapshot={_SNAPSHOT_TOTAL_EXTRACTS} now={len(rows)}")

    if skipped_pdf_errors:
        print(f"\n{len(skipped_pdf_errors)} book(s) skipped (PDF read errors) — see SKIP lines above.")

    return 0 if invariant_held else 1


def main(argv: Optional[list[str]] = None) -> int:
    import asyncio

    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
