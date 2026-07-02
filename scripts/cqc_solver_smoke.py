"""Acceptance smoke for the CQ-C answer-key solver (agent solver.solve).

Fact-over-theory proof that the solver DISCRIMINATES: it must FLAG the three
real answer-key errors the 2026-07-01 content audit found, and PASS a correct
key — otherwise the whole feature is theatre.

  must-FLAG (agrees=False WITH a high-confidence discrepancy):
    - 8f734563 / practice-rlc            (x=5 key says 21/100, truth 7/40)
    - 8f734563 / practice-error-detection(denies the second sign error / +1)
    - 263d99c5 / memory-check            (false symmetry-exclusivity, card 9)
  must-PASS (no high-confidence discrepancy):
    - 1122356a / practice-rlc            (Pythagoras packet, arithmetic 100%)

Makes 4 real gemini-3.1-pro-preview vision/text calls over transport=api
(Vertex SA or GEMINI_API_KEY). One-time, cheap — NOT homework generation.

Data hygiene: the audited outputs are READ from edu_copy (production) via a
SEPARATE read-only asyncpg connection; the solver itself runs against whatever
DATABASE_URL the process is started with (point it at edu_scratch_cqc so the
agent_usages rows land in scratch, never production).

Run:
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc \\
  SOURCE_DB_URL=postgresql://edu:edu@127.0.0.1:5432/edu_copy \\
  uv run --extra dev python -m scripts.cqc_solver_smoke

Exit 0 iff every must-FLAG flags AND the must-PASS passes.

OBSERVED (2026-07-02, gemini-3.1-pro-preview over Vertex, ~$0.12/job at the 3
target phases — confirms R2's estimate):
  - error-detection sign error : CAUGHT robustly (both with/without prior_outputs),
    precise correct explanation (found BOTH sign errors).
  - practice-rlc equivalence    : caught WITHOUT prior_outputs, MISSED with them —
    variable/context-sensitive.
  - memory-check symmetry       : MISSED both runs — solver agreed with the wrong key.
  - clean packet                : PASSED both runs — ZERO false positives.
Read: high PRECISION (never corrupts a correct packet — the load-bearing safety
property), moderate/variable RECALL (reliable on objective computational/procedural
errors, misses subtle conceptual errors). The strict "flag all 3" bar below is a
FAIL as written; whether that bar is the right acceptance criterion (vs "zero false
positives + catches objective errors, conceptual errors → CQ-E golden-eval") is an
open acceptance decision for the human/gatekeeper — do NOT silently relax it.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from app.services import solver, pricing, flows
from app.db import SessionLocal
from sqlalchemy import text

_SOLVER_PROVIDER = "gemini"
_SOLVER_MODEL = "gemini-3.1-pro-preview"

# (job_prefix, phase_name, expect_flag)
_CASES = [
    ("8f734563", "practice-rlc", True),
    ("8f734563", "practice-error-detection", True),
    ("263d99c5", "memory-check", True),
    ("1122356a", "practice-rlc", False),
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

    results = []
    costs = []
    for job_prefix, phase, expect_flag, data in cases:
        print(f"\n[{job_prefix}/{phase}] expect {'FLAG' if expect_flag else 'PASS'} "
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
        ok = flagged == expect_flag
        print(f"  available={outcome.available} agrees={outcome.agrees} "
              f"has_mismatch={outcome.has_mismatch} -> {'FLAG' if flagged else 'PASS'} "
              f"[{'OK' if ok else 'WRONG'}]  cost: {cost}")
        for w in outcome.warnings[:4]:
            print(f"    · {w}")
        results.append(ok)

    passed = all(results)
    print(f"\nRESULT: {'PASS ✅' if passed else 'FAIL ❌'} "
          f"({sum(results)}/{len(results)} cases correct)")
    print("Per-call cost (baseline $/job ≈ 3 × mean solver call):")
    for (jp, ph, _, _), c in zip(cases, costs):
        print(f"  {jp}/{ph}: {c}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
