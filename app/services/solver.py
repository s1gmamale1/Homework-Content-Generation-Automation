"""Independent re-solve second opinion on a generated answer key.

`solve()` re-derives the answer to every item in a generated phase from
first principles (the current lesson's concepts only) and reports where the
generated key disagrees. It is a near-clone of `phase_judge.judge()`: same
single-call shape and unavailable outcomes for ordinary call/parse failures.
The pipeline decides whether a prior defect still blocks acceptance. Only a
`high`-confidence discrepancy triggers a regen (Task 7) — low/medium are
advisory, to avoid false-positive regens (the validate_toc lesson).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from loguru import logger

from app.schemas.solver import Discrepancy, SolveVerdict
from app.services import agent
from app.services.errors import (
    CancelWonSignal, LeaseLostSignal, SessionLimitPause, SlotSaturation,
    TransientPhaseError, is_slot_saturation,
)
from app.services.phase_judge import _is_auth_error, _is_refusal
from app.services.prompts import get_prompt


@dataclass
class SolveOutcome:
    available: bool          # False = solver call/parse failed (degraded)
    agrees: bool              # meaningful only when available
    warnings: list[str] = field(default_factory=list)
    feedback: str = ""        # regen prompt addendum (empty when agrees/unavailable)
    has_mismatch: bool = False  # any HIGH-confidence discrepancy -> triggers the one regen
    refused: bool = False     # solver declined on content policy
    failure: BaseException | None = None  # degraded real exception, for post-mismatch policy


_INSTRUCTIONS = (
    "You are an expert who independently SOLVES each item, then checks the "
    "provided answer key, every option, and the feedback.\n\n"
    "Solve using ONLY the current lesson's concepts (respect the CURRICULUM "
    "BOUNDARY note in the lesson context — if a key is 'correct' only by using "
    "next-lesson material, that is a discrepancy).\n\n"
    "Independently judge EVERY option against the exact question wording, not only "
    "the marked key. A defensible second answer in a single-answer item, no answer "
    "under the wording, or feedback that rejects a defensible answer is a discrepancy. "
    "Check mathematical equivalence (1/2 and 0.5), language synonyms and "
    "context-dependent alternatives, overlapping scientific categories (a whale is "
    "both a mammal and a vertebrate), and historical terminology (sovereign and "
    "monarch may both fit). Do not assume distractors are wrong because unmarked. "
    "Re-solve sentence blanks with each word-bank option and check accepted "
    "alternatives, grammar, meaning and feedback. Check wrong numeric answers, "
    "wrong 'correct versions', and misidentified broken blocks. Quote the question, "
    "options or feedback that establish the discrepancy in your explanation. "
    "Only student-visible premises count as evidence; hidden keys, rubrics, "
    "metadata and earlier student answers cannot supply missing premises. "
    "Accept genuinely open tasks with sufficient evidence and aligned rubrics; "
    "do not demand a unique answer or a hidden key for these tasks. Do NOT flag "
    "harmless phrasing, ordering or formatting. Set confidence honestly; reserve "
    "`high` for demonstrable errors; low/medium remain advisory. "
    # Maintainer evidence for the concrete Uzbek example below: Alisher Navoiy
    # University narrative dictionary, "Karvonsaroy" definition (accessed in the
    # 2026-09-05 review): https://narrativedictionary.navoiy-uni.uz/uz/Dictionary/ViewWord/1bc1c585-7000-41b8-bd21-c80b123ac843
    "Curriculum scope restricts methods, not ordinary word meanings: an "
    "unmentioned synonym is not thereby false. Wrong-option feedback is an "
    "untrusted claim, not an authoritative definition. For example, Uzbek rabot "
    "has a caravanserai meaning as well as a fortified-post meaning. If a "
    "question broadly asks the name of a place where caravans lodge, Karvonsaroy "
    "and Rabot are both defensible; feedback treating Rabot as exclusively "
    "military is false. Judge the exact wording: a question explicitly asking "
    "which term the supplied text uses may distinguish synonyms. Apply the same "
    "ambiguity check to synonyms and overlapping categories in every subject and "
    "language. This solver reports answer correctness only; leave phase item "
    "counts, heading format, workload and style compliance to the separate "
    "contract judge. "
    "If every key, option and feedback is correct, return `agrees=true` with an "
    "empty list."
)

_BOSS_ARENA_ADDENDUM = (
    "## This is a Boss Arena phase — a different shape\n"
    "Boss Arena questions are OPEN Why/How/What reasoning prompts. There is NO "
    "marked-correct option and NO written answer-key field — do NOT expect one, "
    "and do NOT flag a question for 'missing a key'.\n\n"
    "Instead, check each question for an EMBEDDED, OBJECTIVELY-DECIDABLE claim — "
    "a computable value, a mathematical truth/possibility, or a fact the lesson's "
    "concepts settle unambiguously — that the question STATES or ASSUMES as "
    "correct anywhere in its Scenario, its What/counterfactual, or its three "
    "Feedback lines (Correct/Partial/Wrong). Independently derive that claim from "
    "the lesson's concepts. Flag a discrepancy ONLY when the question asserts or "
    "assumes an objectively WRONG answer (e.g. it treats a constructible figure "
    "as impossible, or states a wrong numeric result): set `generated_key` to the "
    "answer the question assumes, `solver_answer` to the correct one, and reserve "
    "`high` for an unambiguous objective error.\n\n"
    "If a question is genuinely OPEN — interpretive, evaluative, design/opinion, "
    "or admitting several defensible answers — it has NO objective key: treat it "
    "as agreeing when supplied evidence is sufficient and its feedback/rubric "
    "accepts defensible answers. Never flag harmless phrasing, difficulty, pedagogy, "
    "hint quality, or the Why/How/What structure."
)

# Per-phase solver-contract addenda appended to _INSTRUCTIONS for phases whose
# shape differs from the standard marked-key phases. Absent phase → no addendum.
_PHASE_SOLVE_ADDENDUM = {
    "boss-arena": _BOSS_ARENA_ADDENDUM,
    "case-based-preview": (
        "Case-based preview: inspect checkpoints, examples, implied answers and "
        "feedback throughout the ten sections. Open prompts need sufficient "
        "visible evidence and aligned rubrics, not an invented unique key."
    ),
    "practice-sentence": (
        "Sentence-fill: independently fill every blank using its visible sentence "
        "and context; test all word-bank entries and defensible alternatives. "
        "Check accepted answers and feedback, without rewriting the output grammar."
    ),
}


def _build_solver_prompt(
    *, contract: str, phase_output_md: str, phase_name: Optional[str] = None
) -> str:
    instructions = _INSTRUCTIONS
    addendum = _PHASE_SOLVE_ADDENDUM.get(phase_name or "")
    if addendum:
        instructions = f"{instructions}\n\n{addendum}"
    parts = [
        instructions,
        "\n\n## CONTRACT (the authoring instructions the output was produced from)",
        contract.strip(),
        "\n\n## OUTPUT TO CHECK (the generated phase, including its answer key)",
        phase_output_md.strip(),
        "",
    ]
    return "\n".join(parts)


def _serialize(discrepancies: list[Discrepancy]) -> list[str]:
    return [
        f"[{d.confidence}] {d.item}: key says '{d.generated_key}', solved "
        f"answer is '{d.solver_answer}' — {d.explanation}"
        for d in discrepancies
    ]


def _build_feedback(discrepancies: list[Discrepancy]) -> str:
    bullets = "\n".join(f"- {line}" for line in _serialize(discrepancies))
    return (
        "\n\n## Fix these answer-key errors\n"
        "An independent solve found these answer-key errors. Correct ALL of "
        "them and regenerate the full deliverable:\n"
        f"{bullets}"
    )


async def solve(
    *,
    subject: str,
    phase_name: str,
    phase_output_md: str,
    lesson_context: Optional[str],
    prior_outputs: dict[str, str],
    output_language: str = "uz",
    solver_provider: str,
    solver_model: Optional[str],
    transport: str = "cli",
    homework_job_id: Optional[UUID] = None,
    phase_output_id: Optional[UUID] = None,
    contract_override: Optional[str] = None,
) -> SolveOutcome:
    """Independently re-solve `phase_output_md`'s items and report where the
    generated answer key is wrong. Returns a SolveOutcome; ordinary CLI/parse
    failures degrade to 'unavailable'. Typed control signals propagate. For
    api transport an auth/401 error is RE-RAISED instead of swallowed: an api
    job must fail loudly, not ship unsolved."""
    try:
        contract = contract_override or get_prompt(subject, phase_name, output_language=output_language)
        solver_prompt = _build_solver_prompt(
            contract=contract, phase_output_md=phase_output_md, phase_name=phase_name)
        result = await agent.run_phase(
            provider=solver_provider,
            model=solver_model,
            phase_prompt=solver_prompt,
            phase_name="__solver__",          # NOT in _VISUAL_PHASES -> no visual-rules noise
            schema=SolveVerdict,
            lesson_context=lesson_context,
            prior_outputs=prior_outputs,
            difficulty=None,
            operation=f"solve:{phase_name}",
            homework_job_id=homework_job_id,
            phase_output_id=phase_output_id,
            transport=transport,
        )
        verdict = result.parsed
        if not isinstance(verdict, SolveVerdict):
            raise RuntimeError("solver produced no parsed SolveVerdict")
    except (CancelWonSignal, LeaseLostSignal, SessionLimitPause, SlotSaturation, TransientPhaseError):
        raise
    except Exception as exc:  # noqa: BLE001 — unavailable review is recorded separately
        if is_slot_saturation(exc):
            raise SlotSaturation(str(exc))  # park the job — do not ship unsolved
        # An api job that hit an auth/401 error must fail LOUDLY (job-level
        # failure), not silently ship unsolved via the degrade path below.
        if transport == "api" and _is_auth_error(exc):
            logger.error(f"solver api auth failure for {phase_name}: {exc!r}")
            raise
        if _is_refusal(exc):
            logger.warning(f"solver refused (content policy) for {phase_name}: {exc!r}")
            return SolveOutcome(
                available=False, refused=True, agrees=True,
                warnings=["solver-refused: content policy"], feedback="", failure=exc,
            )
        logger.warning(f"solver unavailable for {phase_name}: {exc!r}")
        return SolveOutcome(
            available=False, agrees=True,
            warnings=[f"solver-unavailable: {type(exc).__name__}"], feedback="", failure=exc,
        )

    if not verdict.discrepancies:
        return SolveOutcome(available=True, agrees=True, warnings=[], feedback="")
    warnings = _serialize(verdict.discrepancies)
    high_conf = [d for d in verdict.discrepancies if d.confidence == "high"]
    has_mismatch = bool(high_conf)
    return SolveOutcome(
        available=True, agrees=False, warnings=warnings,
        feedback=_build_feedback(high_conf) if has_mismatch else "",
        has_mismatch=has_mismatch,
    )
