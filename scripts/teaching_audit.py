"""Closed-book simulated-student audit for one generated homework packet.

Usage:
  uv run python scripts/teaching_audit.py --job <job-id>
  uv run python scripts/teaching_audit.py --job <job-id> --sensitivity

Derives an exam from the TEXTBOOK lesson pages, sits a simulated closed-book
student before and after "studying" the packet, and reports the per-objective
matrix (already_known / learned / not_taught / not_learnable) + $ cost.
--sensitivity runs the PAIRED experiment (one exam + one pre-test + one grading
call shared; real packet vs empty control) and reports whether the instrument
detects the difference. All calls run transport=api (cli transport is retired).
Exit 0 always unless --strict (then 1 on a failed verdict / failed sensitivity).

Cost note: the printed $ sums the successful attempt of each logical call;
structured-output retries log extra agent_usages rows the CLI total does not
include, so real spend may be marginally higher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from app.services import pricing, teaching_audit as ta  # noqa: E402


def _cost(calls: list[dict]) -> float:
    return sum(
        pricing.cost_usd(c["provider"], c["model"], c["usage"])
        for c in calls
        if c.get("usage")
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--job", required=True, help="homework_jobs id of the packet to audit")
    p.add_argument("--sensitivity", action="store_true",
                   help="paired 7-call run: real packet vs empty control, shared exam+pretest+grade")
    p.add_argument("--out", default=None,
                   help="JSON report path (default var/teaching_audit/<job8>[-sensitivity].json)")
    p.add_argument("--provider", default="gemini")
    p.add_argument("--examiner-model", default="gemini-2.5-pro")
    p.add_argument("--student-model", default="gemini-2.5-flash")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 when not teaching-equivalent/learnable (or sensitivity fails)")
    return p.parse_args(argv)


def _write_report(args: argparse.Namespace, payload: dict, suffix: str) -> None:
    out = pathlib.Path(args.out) if args.out else (
        _REPO_ROOT / "var" / "teaching_audit" / f"{args.job[:8]}{suffix}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")


async def _run(args: argparse.Namespace) -> int:
    kw = dict(provider=args.provider, examiner_model=args.examiner_model,
              student_model=args.student_model)

    if args.sensitivity:
        paired = await ta.paired_audit(args.job, **kw)
        print(ta.render_markdown(paired.normal))
        print()
        print(ta.render_markdown(paired.control))
        verdict = "PASS" if paired.sensitivity_pass else "FAIL"
        print(f"\nsensitivity: {verdict} (learned real={paired.normal.learned_count} "
              f"control={paired.control.learned_count})")
        failures = paired.sensitivity_failures()
        for reason in failures:
            print(f"  - {reason}")
        cost = _cost(paired.calls)
        print(f"cost: ${cost:.4f} across {len(paired.calls)} logical calls "
              f"(examiner {args.examiner_model}, student {args.student_model}, api)")
        _write_report(args, {
            "normal": ta.result_to_dict(paired.normal),
            "control": ta.result_to_dict(paired.control),
            "sensitivity_pass": paired.sensitivity_pass,
            "sensitivity_failures": failures,
            "calls": [dict(c) for c in paired.calls],
            "cost_usd": cost,
        }, "-sensitivity")
        # r24 T1 R5: the verdict now keys to the CORE subset (full-set numbers
        # are still reported above via render_markdown/result_to_dict, just no
        # longer the gate).
        ok = (paired.sensitivity_pass and paired.normal.core_teaching_equivalent
              and paired.normal.core_learnable)
        return 1 if (args.strict and not ok) else 0

    result = await ta.audit_job(args.job, **kw)
    print(ta.render_markdown(result))
    cost = _cost(result.calls)
    print(f"\ncost: ${cost:.4f} across {len(result.calls)} logical calls "
          f"(examiner {args.examiner_model}, student {args.student_model}, api)")
    payload = ta.result_to_dict(result)
    payload["cost_usd"] = cost
    _write_report(args, payload, "")
    # r24 T1 R5: the verdict now keys to the CORE subset (full-set numbers are
    # still reported above via render_markdown/result_to_dict, just no longer
    # the gate).
    ok = result.core_teaching_equivalent and result.core_learnable
    return 1 if (args.strict and not ok) else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ta.TeachingAuditError as exc:
        raise SystemExit(f"teaching-audit failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
