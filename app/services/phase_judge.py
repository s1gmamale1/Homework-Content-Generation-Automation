"""Self-verifying LLM phase validator.

`judge()` grades a generated phase against its own prompt contract
(`get_prompt(subject, phase)`), seeing exactly the generator's inputs
(contract + lesson_context + declared prior_outputs + the output under review).
A single CLI call lists contract violations — each citing the exact offending
text — then refutes its own list, dropping anything it cannot substantiate, so a
hallucinated failure never triggers a needless regeneration. The judge model is
one capability tier above the ACTUAL producer (`model_tiers`). CLI/parse errors
return "unavailable"; the pipeline retains known-major blocking state. Typed
control signals and API authentication failures propagate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from loguru import logger
from pydantic import BaseModel

from app.services import agent
from app.services.errors import (
    CancelWonSignal, LeaseLostSignal, SessionLimitPause, SlotSaturation,
    TransientPhaseError, is_slot_saturation,
)
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
    refused: bool = False    # judge declined on content policy (distinct from a transient error)


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
    "instead of creating one. A correctly-emitted decorative placeholder is COMPLIANT. "
    "A placeholder is NOT supplied visual evidence: if solving requires unseen "
    "features of that image, flag the missing evidence as major. A fabricated "
    "image or an invented http(s) image URL IS a violation — the contract forbids it."
    "\n\nSemantic answerability (all subjects and output languages): independently "
    "check visible prerequisites and every premise needed by each question. Hidden "
    "answer keys, feedback, rubrics, metadata, and previous student answers are not "
    "student-visible supplied evidence. Quote the exact question and the missing "
    "required evidence, or the conflicting text, before alleging a major. Check "
    "that open reasoning tasks have sufficient supplied evidence and that their "
    "rubrics can fairly grade defensible answers under the actual wording. Reject "
    "references to unavailable passages, sources, data or visuals when these are "
    "needed to answer. Flag grade-inappropriate untaught methods required for "
    "success, misleading extra certainty that drops lesson qualifiers, and "
    "cross-phase repeated application that merely repeats an earlier task's "
    "scenario and reasoning. Prior outputs let you inspect repetition; they do "
    "not prove a learner has supplied an answer. Preserve target-language practice "
    "separately from scaffolding/output language in L2 subjects."
    "\n\nReference clarity: A question or reflection that tells or implies the "
    "learner should inspect an absent map, chart, passage or source violates "
    "the present-references-only contract even when visible facts suffice "
    "to answer. Quote that wording and flag it as minor if it only creates "
    "unnecessary confusion; retain major only when necessary evidence is "
    "actually missing. Do not flag an explicitly decorative placeholder, "
    "a general mention of maps as a topic, or an answerable question that "
    "directs the learner to supplied text or data."
)

_FIDELITY_RULE = (
    "\n\nSource-fidelity (CRITICAL): a LESSON CONTEXT section is provided below — the lesson "
    "the output was authored from. Treat it as ground truth for contradictions: raise a "
    "`major` failure for any factual claim ABOUT THE WORLD in the OUTPUT that CONTRADICTS "
    "the LESSON CONTEXT (a changed date, number, name, definition, rule, or causal claim). "
    "A world claim that is merely ABSENT from the LESSON CONTEXT but not contradicted by it "
    "(supporting context, standard curriculum facts) is at most `minor` on absence "
    "alone. Exceptions: missing REQUIRED evidence or premises, fabricated source "
    "authors, quotations, data or provenance, and unsupported claims needed to make "
    "a rubric answerable are `major` when demonstrated by quoted evidence. Do not "
    "turn every unmentioned ordinary fact into a major. Retain the lesson's facts "
    "and qualifiers except an explicitly documented lesson-scoped correction. "
    "DO NOT flag numbers the OUTPUT generates for teaching — "
    "practice-problem values, worked-example arithmetic, invented student names, hypothetical "
    "scenarios — these are expected and are NOT fidelity violations. A hint list of candidate "
    "issues may appear below; verify each against the LESSON CONTEXT before trusting it, and "
    "drop any you cannot substantiate."
)


_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
# math/exercise cues near a number => generated, never a world-claim
_MATH_CUES = ("=", "x ", " x", "solve", "equation", "calculate", "simplify",
              "÷", "×", "·", "√", "step", "answer:", "problem")


def _fidelity_flags(output_md: str, lesson_context: Optional[str]) -> list[str]:
    """ADVISORY ONLY (never gates a regen): surface declarative world-claim YEARS in the
    output that are absent from the source. Deliberately narrow — years only, and only when
    no math/exercise cue sits on the same line — so generated exercise numbers never flag
    (the R14 regen-tax guard). The LLM judge adjudicates these hints."""
    src = (lesson_context or "")
    if not src.strip():
        return []
    src_years = set(_YEAR_RE.findall(src))
    flags: list[str] = []
    for line in output_md.splitlines():
        low = line.lower()
        if any(cue in low for cue in _MATH_CUES):
            continue                                   # generated/teaching numbers — skip
        for y in _YEAR_RE.findall(line):
            if y not in src_years and not any(y in f for f in flags):
                flags.append(f"output states year {y} as fact; not found in source")
    return flags[:8]                                   # cap the hint list


def _build_judge_prompt(
    *, contract: str, output_md: str, fidelity_flags: Optional[list[str]] = None,
) -> str:
    parts = [
        _INSTRUCTIONS + _FIDELITY_RULE,
        "\n\n## CONTRACT (the authoring instructions the output must satisfy)",
        contract.strip(),
    ]
    if fidelity_flags:
        parts += [
            "\n## POSSIBLE SOURCE ISSUES (hints — verify against LESSON CONTEXT before trusting)",
            "\n".join(f"- {f}" for f in fidelity_flags),
        ]
    parts += ["\n## OUTPUT UNDER REVIEW", output_md.strip(), ""]
    return "\n".join(parts)


def _serialize_failures(failures: list[Failure]) -> list[str]:
    return [f"[{f.severity}] {f.requirement} — {f.evidence}" for f in failures]


# Substrings (case-insensitive) that mark an auth/401 failure. An api job that
# hits one of these must fail loudly rather than ship unjudged. Covers the
# claude shapes (401 / invalid api key) AND the gemini/Vertex shapes the
# fleet-api-6 service-account path can produce (PERMISSION_DENIED 403, AIza
# key rejection, SA token-mint failure, UNAUTHENTICATED). Deliberately NO bare
# "403": exception strings can embed generated output, and a stray "403" there
# would loudly fail an api job that should only soft-degrade.
_AUTH_SIGNALS = (
    "401",
    "invalid api key",
    "invalid_api_key",
    "unauthorized",
    "permission_denied",
    "permission denied",
    "api key not valid",
    "invalid_grant",
    "unauthenticated",
)


def _is_auth_error(exc: BaseException) -> bool:
    # Typed first (Phase 4.1 §5a): agent._auth_env raises AuthEnvError for
    # credential mispredictions whose messages match NO substring signal —
    # isinstance classification, never substring luck.
    if isinstance(exc, agent.AuthEnvError):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in _AUTH_SIGNALS)


# Anchored first-person-decline phrases that mark a content-policy REFUSAL (the
# judge emitted prose instead of a Verdict, so run_phase exhausted schema retries
# and raised — the refusal text rides in the exception via _failure_preview).
# Deliberately anchored: must NOT match a verbose-but-substantive judge answer
# ("the output violates requirement 3"), a schema-validation error, or a CLI error.
_REFUSAL_SIGNALS = (
    "i cannot assist", "i can't assist",
    "i cannot help", "i can't help",
    "i am unable to assist", "i'm unable to assist",
    "i am unable to help", "i'm unable to help",
    "i must decline", "i cannot comply", "i can't comply",
    "i will not provide", "i won't provide",
    "i cannot create", "i can't create",
    "against my guidelines", "violates content polic",
)


def _is_refusal(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in _REFUSAL_SIGNALS)


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
    judge_provider: str,
    judge_model: Optional[str],
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    transport: str = "cli",
    contract_override: Optional[str] = None,
    output_language: str = "uz",
) -> JudgeOutcome:
    """Grade `output_md` against its phase contract (the custom override when
    supplied, else the built-in prompt). Returns a JudgeOutcome;
    Ordinary CLI/parse errors degrade to 'unavailable', which is not a verified
    pass. Control signals propagate. For api transport an auth/401 error is RE-RAISED instead of
    swallowed: an api job must fail loudly, not ship unjudged."""
    try:
        # judge_provider/judge_model are resolved upstream by
        # model_tiers.resolve_judge (per-role override + self-grade guard); use
        # them as-given. contract_override carries a per-phase custom prompt.
        contract = contract_override or get_prompt(subject, phase_name, output_language=output_language)
        flags = _fidelity_flags(output_md, lesson_context)   # C3 advisory year-fidelity hints
        judge_prompt = _build_judge_prompt(
            contract=contract, output_md=output_md, fidelity_flags=flags,
        )
        result = await agent.run_phase(
            provider=judge_provider,
            model=judge_model,
            phase_prompt=judge_prompt,
            phase_name="__judge__",          # NOT in _VISUAL_PHASES -> no visual-rules noise
            schema=Verdict,
            lesson_context=lesson_context,
            prior_outputs=prior_outputs,
            difficulty=None,
            operation=f"judge:{phase_name}",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            transport=transport,
        )
        verdict = result.parsed
        if not isinstance(verdict, Verdict):
            raise RuntimeError("judge produced no parsed Verdict")
    except (CancelWonSignal, LeaseLostSignal, SessionLimitPause, SlotSaturation, TransientPhaseError):
        raise
    except Exception as exc:  # noqa: BLE001 — unavailable review is recorded separately
        if is_slot_saturation(exc):
            raise SlotSaturation(str(exc))  # park the job — do not ship unjudged
        # An api job that hit an auth/401 error must fail LOUDLY (job-level
        # failure), not silently ship unjudged via the degrade path below.
        if transport == "api" and _is_auth_error(exc):
            logger.error(f"phase_judge api auth failure for {phase_name}: {exc!r}")
            raise
        if _is_refusal(exc):
            logger.warning(f"phase_judge refused (content policy) for {phase_name}: {exc!r}")
            return JudgeOutcome(
                available=False, refused=True, passed=True,
                warnings=["judge-refused: content policy"], feedback="",
            )
        logger.warning(f"phase_judge unavailable for {phase_name}: {exc!r}")
        return JudgeOutcome(
            available=False, passed=True,
            warnings=[f"judge-unavailable: {type(exc).__name__}"], feedback="",
        )

    if not verdict.failures:
        return JudgeOutcome(available=True, passed=True, warnings=[], feedback="")
    warnings = _serialize_failures(verdict.failures)
    has_major = any(f.severity == "major" for f in verdict.failures)
    return JudgeOutcome(
        available=True, passed=False, warnings=warnings,
        feedback=_build_feedback(warnings), has_major=has_major,
    )
