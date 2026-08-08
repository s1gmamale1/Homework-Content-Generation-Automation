"""Calibrate the warn-only extract-completeness check against the labeled
coverage-audit dataset (9 lessons, 8 known extract-omissions).

Read-only against edu_copy; the only writes are the agent_usages rows the check
itself records. Bounded: 9 lessons x 1 call per model. Prints token + $ totals
for the money-rule log.

If this aborts on the success-count check, suspect the app-side DB write path as
well as the calls themselves: _record_usage is best-effort and SWALLOWS write
failures (agent.py:814-815), so a wrong DATABASE_URL makes healthy calls look
like missing ones. That is the fail-safe direction — it never turns a broken run
into a passing score — but it is the first thing to check.

MUST be run as a MODULE (-m), not by path: this repo has no [build-system], so
`app` is never installed into the venv and only resolves from the repo root —
running `python scripts/<name>.py` puts scripts/ on sys.path[0] and dies with
`ModuleNotFoundError: No module named 'app'` (verified empirically). Same idiom
as scripts/cqd_extract_guards_smoke.py.

Run (from the worktree, env exported EXPLICITLY — the worktree has no .env, so
config.py walks up to /Users/macmini5/Documents/.env, whose DATABASE_URL points
at a REMOTE host. Both DSNs below must name the same server, or the usage rows
this script writes land somewhere the cost query never looks):

  cd /Users/macmini5/Documents/HCGA-extract-coverage
  export GEMINI_API_KEY=...            # plain key, not Vertex SA
  export DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/edu_copy
  export CALIBRATE_DSN=postgresql://edu:edu@127.0.0.1:5432/edu_copy
  export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
  uv run python -m scripts.extract_coverage_calibrate gemini-3.5-flash-lite
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import asyncpg

import app.config  # noqa: F401 — triggers load_dotenv
from app.services import agent, content_lint

MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.5-flash-lite"
DSN = os.environ.get("CALIBRATE_DSN", "postgresql://edu:edu@127.0.0.1:5432/edu_copy")
DATA = Path("docs/research/2026-07-06-coverage-audit-data.json")

# The worktree has NO var/ — the host's book store lives in the main checkout.
# Same trap the CQ-D smoke already had to code around (scripts/
# cqd_extract_guards_smoke.py:33-41); resolve the first root that exists.
_BOOK_ROOTS = [
    Path(os.environ["VAR_DIR"]) / "books" if os.environ.get("VAR_DIR") else None,
    Path.cwd() / "var" / "books",
    Path("/Users/macmini5/Documents/Homework-Content-Generation-Automation/var/books"),
]
BOOKS = next((r for r in _BOOK_ROOTS if r and r.is_dir()), None)
if BOOKS is None:
    raise SystemExit("no book store found — set VAR_DIR to the checkout holding var/books")

# Guard the worktree trap: this MUST be the worktree's code, not the main
# checkout's (a -c script can silently import the other one → false all-clear).
assert "HCGA-extract-coverage" in agent.__file__, f"wrong agent module: {agent.__file__}"


def _matches(labeled: str, reported: str) -> bool:
    """Cross-language fuzzy match: a salient (>=4-char) token of one appears
    INSIDE the other. Substring, not exact-set intersection — Uzbek is
    agglutinative ('izotop' / 'izotoplar' / 'izotoplarning' are three distinct
    exact tokens), so exact matching would score a genuinely-caught omission as
    a miss and could fail hard bar A for matcher reasons, not checker reasons.
    Same containment idiom content_lint.lint_coverage itself uses."""
    a, b = content_lint._norm(labeled), content_lint._norm(reported)
    return (any(t in b for t in content_lint._salient_tokens(labeled))
            or any(t in a for t in content_lint._salient_tokens(reported)))


async def main() -> None:
    rows = json.loads(DATA.read_text())
    conn = await asyncpg.connect(DSN)
    total_hit = total_labeled = total_reported_clean = 0
    evaluated = 0
    report: list[str] = []
    # started_at is stamped by run_phase in THIS process (agent.py:998), so this
    # is the same clock — no skew, and no slack. Slack would only widen the
    # window to the PREVIOUS run's rows: Step 3 runs a second model right after
    # the first, so a backward window would count those and abort a healthy run.
    t0 = datetime.now(timezone.utc)
    try:
        for r in rows:
            job_id = r["job"]
            db = await conn.fetchrow(
                "select p.output_md, j.book_id from homework_jobs j "
                "join phase_outputs p on p.job_id = j.id and p.phase_name = 'extract' "
                "where j.id = $1", UUID(job_id))
            if db is None or not db["output_md"]:
                report.append(f"SKIP {job_id} {r['subject']} {r['sec']}: extract row missing")
                continue
            book_dir = BOOKS / str(db["book_id"])
            pdf = book_dir / "source.pdf"
            if not pdf.exists():
                report.append(f"SKIP {job_id} {r['subject']} {r['sec']}: pdf missing at {pdf}")
                continue
            ps, pe = (int(x) for x in r["pages"].split("-"))
            source = await asyncio.to_thread(
                agent.read_page_range_text, pdf, ps, pe, margin=1)

            misses = await agent.check_extract_coverage(
                summary=db["output_md"], source_text=source,
                section_title=r.get("title") or r["sec"], section_number=r["sec"],
                provider="gemini", model=MODEL, transport="api",
                homework_job_id=None, phase_output_id=None,
            )
            reported = [m.label for m in misses]
            labeled = [i["label"] for i in r["items"] if not i.get("in_extract")]
            hit = [lab for lab in labeled if any(_matches(lab, rep) for rep in reported)]
            extra = [rep for rep in reported
                     if not any(_matches(lab, rep) for lab in labeled)]

            evaluated += 1
            total_labeled += len(labeled)
            total_hit += len(hit)
            if not labeled:
                total_reported_clean += len(reported)

            report.append(
                f"\n{r['subject']} {r['sec']} ({r['pages']}pp, extract {r['extract_chars']} chars)"
                f"\n  labeled misses ({len(labeled)}): " + (" | ".join(labeled) or "(none — CLEAN lesson)") +
                f"\n  reported ({len(reported)}): " + (" | ".join(reported) or "(none)") +
                f"\n  caught {len(hit)}/{len(labeled)}; unlabeled-reported {len(extra)}"
            )
        # check_extract_coverage is fail-open BY CONTRACT: an auth/429/limiter
        # failure returns [] and is indistinguishable from "clean extract". So a
        # broken environment would score zero misses everywhere and PASS hard
        # bar B. Count the successful calls the check actually recorded.
        # model_name too, so a back-to-back second-model run can never count the
        # first run's rows even if the clocks were to collide.
        n_success = await conn.fetchval(
            "select count(*) from agent_usages "
            "where operation = 'lesson.extract.coverage' "
            "and success and started_at >= $1 and model_name = $2", t0, MODEL)
        # Informational ONLY — never an abort condition. run_phase's schema mode
        # records a success=False row under the SAME operation for a first
        # attempt that fails Pydantic validation, then retries and records a
        # success row (agent.py:1137-1156). Aborting on this would fail a
        # perfectly healthy run on one JSON flake — and it buys nothing: a call
        # that ULTIMATELY failed writes no success row, so n_success already
        # catches it.
        n_failed = await conn.fetchval(
            "select count(*) from agent_usages "
            "where operation = 'lesson.extract.coverage' "
            "and not success and started_at >= $1 and model_name = $2", t0, MODEL)
    finally:
        await conn.close()

    print("\n".join(report))
    if n_failed:
        print(f"\nnote: {n_failed} failed check attempt(s) recorded — expected "
              "occasionally in schema mode (first attempt retried).")
    if n_success != evaluated:
        raise SystemExit(
            f"\nABORT: expected {evaluated} successful check call(s), found "
            f"{n_success}. Fewer means fail-open hid a broken call (which then "
            "scores as a clean extract); more means this count caught another "
            "run's rows. Either way the score is not trustworthy — fix and re-run."
        )
    skipped = [line for line in report if line.startswith("SKIP ")]
    if skipped:
        # A partial run fakes the gate: 0/0 recall reads like a pass. Abort loud.
        raise SystemExit(
            f"\nABORT: {len(skipped)} of {len(rows)} lessons could not be evaluated. "
            "The calibration gate is only meaningful over the full labeled set — "
            "fix the DSN / book store and re-run.\n" + "\n".join(skipped)
        )
    print(f"\n=== MODEL {MODEL} ===")
    print(f"recall over labeled extract-losses: {total_hit}/{total_labeled}")
    print(f"items reported on the 4 CLEAN lessons (candidate FPs, hand-check each): "
          f"{total_reported_clean}")
    print("\nTokens/$ — query agent_usages for operation='lesson.extract.coverage' "
          "in the last hour and paste the total into the calibration doc.")


if __name__ == "__main__":
    asyncio.run(main())
