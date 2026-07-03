"""Characterize the CQ-C solver's symmetry/equivalence MISS (gate-requested).

Bounded diagnostic (≤ ~12 real gemini-3.1-pro-preview calls, ~$0.02-0.08 each):

  EXP 1 — raw verdicts (current prompt) for the symmetry (263d99c5/memory-check)
          and equivalence (8f734563/practice-rlc) cases. Distinguishes:
            (a) genuine capability miss  -> agrees=True, empty discrepancies
            (b) thresholding             -> agrees=False, only low/medium discrepancies
                                            suppressed by the high-only regen gate.
  EXP 2 — one prompt variant (adds a TRUTH-VALUE rule for MC keys), re-run ×3 on
          the symmetry item AND ×3 on the must-PASS packet (1122356a/practice-rlc).
          Recovers the flagship case iff symmetry flips to high-conf AND must-PASS
          stays clean (zero-false-positive guard).
  EXP 3 — claude-opus cross-model probe: BLOCKED on this host (no ANTHROPIC_API_KEY,
          Vertex-only fleet). Operator-runnable where a claude key exists.

Reads audited outputs from edu_copy (read-only); runs the solver against whatever
DATABASE_URL is set (point at edu_scratch_cqc).

Run:
  DATABASE_URL=postgresql+asyncpg://macmini5@127.0.0.1:5432/edu_scratch_cqc \\
  SOURCE_DB_URL=postgresql://edu:edu@127.0.0.1:5432/edu_copy \\
  uv run --extra dev python -m scripts.cqc_solver_characterize
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

from app.schemas.solver import SolveVerdict
from app.services import agent, solver, flows, pricing
from app.services.prompts import get_prompt

_PROVIDER = "gemini"
_MODEL = "gemini-3.1-pro-preview"

_TRUTH_VALUE_RULE = (
    "\n\nTRUTH-VALUE RULE (for multiple_choice / choose_correct_explanation / "
    "true-false items): independently determine the TRUTH VALUE of EACH option or "
    "statement on its own mathematical merits FIRST, then compare to which option "
    "the key marks correct. A statement that is objectively TRUE but the key marks "
    "as wrong ('xato'/incorrect), or objectively FALSE but marked correct, is a "
    "DEMONSTRABLE HIGH-confidence error — not a stylistic nuance. Verify every "
    "factual/mathematical claim yourself; do NOT defer to the key's framing."
)


async def _load(src, job_prefix, phase):
    row = await src.fetchrow(
        "select hj.subject, po.output_md from phase_outputs po join homework_jobs hj "
        "on hj.id=po.job_id where left(hj.id::text,8)=$1 and po.phase_name=$2 limit 1",
        job_prefix, phase)
    if row is None:
        raise SystemExit(f"missing {phase} for {job_prefix}")
    extract = await src.fetchval(
        "select po.output_md from phase_outputs po join homework_jobs hj on hj.id=po.job_id "
        "where left(hj.id::text,8)=$1 and po.phase_name='extract' limit 1", job_prefix)
    prior = {}
    for dep in flows.PHASE_DEPS.get(phase, []):
        md = await src.fetchval(
            "select po.output_md from phase_outputs po join homework_jobs hj on hj.id=po.job_id "
            "where left(hj.id::text,8)=$1 and po.phase_name=$2 limit 1", job_prefix, dep)
        if md:
            prior[dep] = flows._strip_svgs(md)
    return {"subject": row["subject"], "md": row["output_md"],
            "ctx": extract or "", "prior": prior}


def _build(instructions, contract, md):
    return "\n".join([instructions, "\n\n## CONTRACT (the authoring instructions the "
                      "output was produced from)", contract.strip(),
                      "\n\n## OUTPUT TO CHECK (the generated phase, including its answer key)",
                      md.strip(), ""])


async def _raw(data, phase, instructions):
    contract = get_prompt(data["subject"], phase, output_language="uz")
    prompt = _build(instructions, contract, data["md"])
    res = await agent.run_phase(
        provider=_PROVIDER, model=_MODEL, phase_prompt=prompt, phase_name="__solver__",
        schema=SolveVerdict, lesson_context=data["ctx"], prior_outputs=data["prior"],
        difficulty=None, operation=f"solve:{phase}", transport="api",
        homework_job_id=None, phase_output_id=None)
    v: SolveVerdict = res.parsed
    u = res.usage or {}
    cost = pricing.cost_usd(_PROVIDER, _MODEL, u)
    return v, cost


def _show(v: SolveVerdict, cost):
    hi = [d for d in v.discrepancies if d.confidence == "high"]
    print(f"    agrees={v.agrees}  discrepancies={len(v.discrepancies)} "
          f"(high={len(hi)})  ${cost:.4f}")
    for d in v.discrepancies:
        print(f"      [{d.confidence}] {d.item}: {d.explanation[:180]}")


async def main() -> int:
    src = await asyncpg.connect(os.environ.get(
        "SOURCE_DB_URL", "postgresql://edu:edu@127.0.0.1:5432/edu_copy"))
    try:
        sym = await _load(src, "263d99c5", "memory-check")
        equiv = await _load(src, "8f734563", "practice-rlc")
        clean = await _load(src, "1122356a", "practice-rlc")
    finally:
        await src.close()

    print("=== EXP 1: raw verdict, CURRENT prompt ===")
    print("  [symmetry 263d99c5/memory-check]")
    _show(*await _raw(sym, "memory-check", solver._INSTRUCTIONS))
    print("  [equivalence 8f734563/practice-rlc]")
    _show(*await _raw(equiv, "practice-rlc", solver._INSTRUCTIONS))

    variant = solver._INSTRUCTIONS + _TRUTH_VALUE_RULE
    print("\n=== EXP 2: TRUTH-VALUE variant, symmetry ×3 (recall) ===")
    for i in range(3):
        print(f"  [symmetry run {i+1}]")
        _show(*await _raw(sym, "memory-check", variant))
    print("=== EXP 2: TRUTH-VALUE variant, must-PASS ×3 (false-positive guard) ===")
    for i in range(3):
        print(f"  [must-PASS 1122356a/practice-rlc run {i+1}]")
        _show(*await _raw(clean, "practice-rlc", variant))

    print("\n=== EXP 3: claude-opus cross-model — BLOCKED (no ANTHROPIC_API_KEY on this host) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
