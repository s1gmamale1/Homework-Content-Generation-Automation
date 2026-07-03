"""Acceptance smoke for the CQ-C answer-key solver (agent solver.solve).

Fact-over-theory proof of what the solver does — and, just as importantly, what
it does NOT do. Against the real 2026-07-01 content-audit packets:

  GATED (hard exit-0 bar):
    - 8f734563 / practice-error-detection : MUST flag  (objective sign error — the
      class the solver reliably catches)
    - 1122356a / practice-rlc (clean key) : MUST pass   (zero-false-positive — the
      load-bearing safety property; never corrupt a correct packet)

  INFORMATIONAL (reported, NOT gated — see the recall boundary below):
    - 8f734563 / practice-rlc     : expression-equivalence error — MISSED
    - 263d99c5 / memory-check     : conceptual truth-value (symmetry) error — MISSED

RECALL BOUNDARY (characterized 2026-07-02 — evidence: scripts/cqc_solver_characterize.py):
  Of the 3 audited "correct-student-graded-wrong" defects, gemini-3.1-pro-preview
  reliably catches 1 (objective sign/arithmetic), and misses 2 (conceptual
  truth-value + expression-equivalence) — a GENUINE capability miss (`agrees=True,
  0 discrepancies`, NOT a suppressed low/medium under the high-only gate), and it
  persists under a truth-value-directive prompt variant (×3) too. Across every run
  the zero-false-positive property held (must-PASS clean ×N). The two missed
  classes are covered only by CQ-E's answer-key audit rubric, not by this solver.
  Do NOT round this up to "2 of 3": it is 1 of 3.

  (A claude-opus cross-model probe was NOT run — gemini-only is standing policy;
  see cqc_solver_characterize.py EXP 3.)

Makes 4 real gemini-3.1-pro-preview calls over transport=api (~$0.12/job at the 3
target phases — confirms R2). One-time — NOT homework generation.

Data hygiene: audited outputs are READ from edu_copy (production) via a SEPARATE
read-only asyncpg connection; the solver runs against whatever DATABASE_URL the
process starts with (point it at edu_scratch_cqc so agent_usages lands in scratch).

Run:
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc \\
  SOURCE_DB_URL=postgresql://edu:edu@127.0.0.1:5432/edu_copy \\
  uv run --extra dev python -m scripts.cqc_solver_smoke

Exit 0 iff BOTH gated cases hold (sign-error flags AND clean key passes). The two
informational cases are printed for the record but never gate.
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

from app.services import solver, pricing, flows
from app.db import SessionLocal
from sqlalchemy import text

_SOLVER_PROVIDER = "gemini"
_SOLVER_MODEL = "gemini-3.1-pro-preview"

# (job_prefix, phase_name, expect_flag, gated)
_CASES = [
    ("8f734563", "practice-error-detection", True, True),   # GATED: objective sign error
    ("1122356a", "practice-rlc", False, True),              # GATED: zero-false-positive
    ("8f734563", "practice-rlc", True, False),              # informational: equivalence (missed)
    ("263d99c5", "memory-check", True, False),              # informational: symmetry (missed)
]


async def _load_case(src: asyncpg.Connection, job_prefix: str, phase: str) -> dict:
    row = await src.fetchrow(
        """
        select hj.subject, po.output_md
          from phase_outputs po join homework_jobs hj on hj.id = po.job_id
         where left(hj.id::text, 8) = $1 and po.phase_name = $2
         limit 1
        """,
        job_prefix, phase,
    )
    if row is None:
        raise SystemExit(f"no {phase} output for job {job_prefix} in source DB")
    extract = await src.fetchval(
        """
        select po.output_md
          from phase_outputs po join homework_jobs hj on hj.id = po.job_id
         where left(hj.id::text, 8) = $1 and po.phase_name = 'extract'
         limit 1
        """,
        job_prefix,
    )
    # Faithful to production: feed the phase its dependency outputs (PHASE_DEPS),
    # SVGs stripped exactly as the pipeline does before injection.
    prior: dict[str, str] = {}
    for dep in flows.PHASE_DEPS.get(phase, []):
        md = await src.fetchval(
            "select po.output_md from phase_outputs po join homework_jobs hj "
            "on hj.id = po.job_id where left(hj.id::text,8)=$1 and po.phase_name=$2 limit 1",
            job_prefix, dep,
        )
        if md:
            prior[dep] = flows._strip_svgs(md)
    return {"subject": row["subject"], "output_md": row["output_md"],
            "lesson_context": extract or "", "prior_outputs": prior}


async def _latest_solve_cost(phase: str) -> str:
    """Read back the agent_usages row the solve() call just wrote (scratch DB)."""
    async with SessionLocal() as s:
        r = (await s.execute(text(
            "select prompt_tokens, output_tokens, cached_tokens, model_name "
            "from agent_usages where operation = :op order by created_at desc limit 1"
        ), {"op": f"solve:{phase}"})).first()
    if r is None:
        return "(no usage row)"
    usage = {"prompt_tokens": r.prompt_tokens or 0, "output_tokens": r.output_tokens or 0,
             "cached_tokens": r.cached_tokens or 0}
    cost = pricing.cost_usd(_SOLVER_PROVIDER, r.model_name or _SOLVER_MODEL, usage)
    return (f"in={usage['prompt_tokens']} out={usage['output_tokens']} "
            f"cached={usage['cached_tokens']} ${cost:.4f}")


async def main() -> int:
    src_url = os.environ.get("SOURCE_DB_URL", "postgresql://edu:edu@127.0.0.1:5432/edu_copy")
    src = await asyncpg.connect(src_url)
    try:
        cases = [(*c, await _load_case(src, c[0], c[1])) for c in _CASES]
    finally:
        await src.close()

    gated_ok = []
    costs = []
    for job_prefix, phase, expect_flag, gated, data in cases:
        tag = "GATED" if gated else "info"
        print(f"\n[{tag}] [{job_prefix}/{phase}] expect {'FLAG' if expect_flag else 'PASS'} "
              f"(subject={data['subject']})")
        outcome = await solver.solve(
            subject=data["subject"], phase_name=phase,
            phase_output_md=data["output_md"], lesson_context=data["lesson_context"],
            prior_outputs=data["prior_outputs"], output_language="uz",
            solver_provider=_SOLVER_PROVIDER, solver_model=_SOLVER_MODEL, transport="api",
            homework_job_id=None, phase_output_id=None,
        )
        cost = await _latest_solve_cost(phase)
        costs.append(cost)
        flagged = outcome.available and not outcome.agrees and outcome.has_mismatch
        matched = flagged == expect_flag
        verdict = "OK" if matched else ("MISS — known recall gap" if not gated else "WRONG")
        print(f"  available={outcome.available} agrees={outcome.agrees} "
              f"has_mismatch={outcome.has_mismatch} -> {'FLAG' if flagged else 'PASS'} "
              f"[{verdict}]  cost: {cost}")
        for w in outcome.warnings[:4]:
            print(f"    · {w}")
        if gated:
            gated_ok.append(matched)

    passed = all(gated_ok)
    print(f"\nRESULT: {'PASS ✅' if passed else 'FAIL ❌'} "
          f"(gated: {sum(gated_ok)}/{len(gated_ok)} — sign-error flags + must-PASS clean)")
    print("Recall (informational): of 3 audited must-FLAG defects, solver catches 1 "
          "(objective sign/arithmetic), misses 2 (conceptual + equivalence) on "
          "gemini-3.1-pro. Missed classes → CQ-E audit rubric.")
    print("Per-call cost (baseline $/job ≈ 3 × mean solver call):")
    for (jp, ph, *_), c in zip(cases, costs):
        print(f"  {jp}/{ph}: {c}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
