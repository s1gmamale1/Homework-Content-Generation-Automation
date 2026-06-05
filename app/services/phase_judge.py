"""Self-verifying LLM phase validator.

`judge()` grades a generated phase against its own prompt contract
(`get_prompt(subject, phase)`), seeing exactly the generator's inputs
(contract + lesson_context + declared prior_outputs + the output under review).
A single CLI call lists contract violations — each citing the exact offending
text — then refutes its own list, dropping anything it cannot substantiate, so a
hallucinated failure never triggers a needless regeneration. The judge model is
one capability tier above the ACTUAL producer (`model_tiers`). On any CLI/parse
error the judge degrades to "unavailable" and never blocks generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from loguru import logger
from pydantic import BaseModel

from app.services import agent, model_tiers
from app.services.prompts import get_prompt


class Failure(BaseModel):
    requirement: str                       # the contract rule the output violates
    evidence: str                          # the exact quote (or quoted absence) proving it
    severity: Literal["major", "minor"]    # major -> regenerate; minor -> warn only


class Verdict(BaseModel):
    passed: bool
    failures: list[Failure] = []


@dataclass
class JudgeOutcome:
    available: bool          # False = judge CLI/parse failed (degraded)
    passed: bool             # meaningful only when available
    warnings: list[str]      # serialized failures, OR ["judge-unavailable: …"]
    feedback: str            # regen prompt addendum (empty when passed/unavailable)
    has_major: bool = False  # any MAJOR failure -> triggers the one regen


_INSTRUCTIONS = (
    "You are a strict reviewer validating a generated homework phase against the "
    "authoring instructions it was produced from. You do not rewrite it; you only "
    "judge compliance.\n\n"
    "Do this in ONE response:\n"
    "1. List every requirement in the CONTRACT that the OUTPUT violates. For each, "
    "quote the EXACT offending text from the OUTPUT (or the exact missing element, "
    "naming where the CONTRACT requires it). No vague 'feels off'.\n"
    "2. Then challenge your own list: for each candidate, confirm it is genuinely "
    "violated by the quoted evidence. DROP any item you cannot substantiate with a "
    "direct citation — treat anything you cannot quote as your own hallucination.\n"
    "3. Output ONLY the survivors.\n"
    "4. For each survivor, set `severity`:\n"
    "   - `major` = breaks the learning purpose or correctness: wrong or missing "
    "content, an answer leaked by a hint, a wrong count of REQUIRED structural "
    "elements (sections / checkpoints / cards / etc.), or omitted key concepts from "
    "the lesson.\n"
    "   - `minor` = stylistic / length / wording / formatting nits that do not harm "
    "the output's usefulness.\n"
    "   Be conservative: mark `major` ONLY when the issue genuinely degrades the "
    "student's learning; default borderline length/wording issues to `minor`.\n\n"
    "Visual rule (do NOT over-flag): the CONTRACT tells the generator to emit "
    "`![placeholder: … — image gen required](placeholder)` for any raster/photo "
    "instead of creating one. A correctly-emitted placeholder is COMPLIANT — never "
    "raise a 'missing image / incomplete visual' failure over it. But a fabricated "
    "image or an invented http(s) image URL IS a violation — the contract forbids it."
)


def _build_judge_prompt(*, contract: str, output_md: str) -> str:
    return (
        f"{_INSTRUCTIONS}\n\n"
        "## CONTRACT (the authoring instructions the output must satisfy)\n"
        f"{contract.strip()}\n\n"
        "## OUTPUT UNDER REVIEW\n"
        f"{output_md.strip()}\n"
    )


def _serialize_failures(failures: list[Failure]) -> list[str]:
    return [f"[{f.severity}] {f.requirement} — {f.evidence}" for f in failures]


def _build_feedback(warnings: list[str]) -> str:
    bullets = "\n".join(f"- {w}" for w in warnings)
    return (
        "\n\n## Fix these (a reviewer rejected your previous attempt)\n"
        "Your previous output violated these contract requirements. Correct ALL of "
        "them and regenerate the full deliverable:\n"
        f"{bullets}"
    )


async def judge(
    *,
    subject: str,
    phase_name: str,
    output_md: str,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    gen_provider: str,
    gen_model: Optional[str],
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
) -> JudgeOutcome:
    """Grade `output_md` against its phase contract. Returns a JudgeOutcome;
    NEVER raises — any error (bad subject/phase, CLI failure, unparseable
    verdict) degrades to 'unavailable' so validation can't block generation."""
    try:
        judge_provider, judge_model = model_tiers.judge_model_for(gen_provider, gen_model)
        contract = get_prompt(subject, phase_name)
        judge_prompt = _build_judge_prompt(contract=contract, output_md=output_md)
        result = await agent.run_phase(
            provider=judge_provider,
            model=judge_model,
            phase_prompt=judge_prompt,
            phase_name="__judge__",          # NOT in _SVG_PHASES -> no SVG noise
            schema=Verdict,
            lesson_context=lesson_context,
            prior_outputs=prior_outputs,
            difficulty=None,
            operation=f"judge:{phase_name}",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
        )
        verdict = result.parsed
        if not isinstance(verdict, Verdict):
            raise RuntimeError("judge produced no parsed Verdict")
    except Exception as exc:  # noqa: BLE001 — judge must NEVER block generation
        logger.warning(f"phase_judge unavailable for {phase_name}: {exc!r}")
        return JudgeOutcome(
            available=False, passed=True,
            warnings=[f"judge-unavailable: {type(exc).__name__}"], feedback="",
        )

    if verdict.passed or not verdict.failures:
        return JudgeOutcome(available=True, passed=True, warnings=[], feedback="")
    warnings = _serialize_failures(verdict.failures)
    has_major = any(f.severity == "major" for f in verdict.failures)
    return JudgeOutcome(
        available=True, passed=False, warnings=warnings,
        feedback=_build_feedback(warnings), has_major=has_major,
    )
