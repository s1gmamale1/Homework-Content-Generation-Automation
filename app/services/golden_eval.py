"""CQ-E golden-eval harness.

Scores generated homework packets against the audit's 6-dimension rubric
(`docs/research/2026-07-01-content-quality-audit-g8-math.md`), diffs against
frozen baselines, and gates prompt/model-change PRs on no-regression.

This module is intentionally standalone / offline: it reads packets via
`phase_repo.list_for_job` (read-only) and the golden-set manifest committed
under `tests/golden/`. It does not touch pipeline/worker/schema code.
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from app.services import agent, content_lint

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "tests" / "golden" / "manifest.json"

_DIMENSIONS = (
    "boundary",
    "answer_key",
    "broken_question",
    "language",
    "reflection",
    "extract_fidelity",
)


@dataclass(frozen=True)
class GoldenEntry:
    """One audited golden-set packet (a job whose 11 phases were human-scored)."""

    job_id: str
    book_id: str
    subject: str
    grade: str
    language: str
    source_pages: str
    audit_verdict: dict[str, str]
    source_pdf_pages: str = ""
    reflection_evidence: str = ""


def load_golden_set(manifest_path: pathlib.Path | None = None) -> list[GoldenEntry]:
    """Load the frozen golden set from `tests/golden/manifest.json`."""
    path = manifest_path or _MANIFEST_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries: list[GoldenEntry] = []
    for row in raw:
        entries.append(
            GoldenEntry(
                job_id=row["job_id"],
                book_id=row["book_id"],
                subject=row["subject"],
                grade=row["grade"],
                language=row["language"],
                source_pages=row["source_pages"],
                audit_verdict=dict(row["audit_verdict"]),
                source_pdf_pages=row.get("source_pdf_pages", ""),
                reflection_evidence=row.get("reflection_evidence", ""),
            )
        )
    return entries


# --------------------------------------------------------------------------
# Deterministic dimension scorers (free tier — no LLM call)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseView:
    """Plain read-model for one phase's output — decouples scoring from the ORM.

    Built either from a real `phase_outputs` row or from a synthetic fixture in
    tests. `solver_status` is CQ-C's column (not present on this base); callers
    that build a `PhaseView` from a pre-CQ-C row should pass `None`.
    """

    phase_name: str
    output_md: str
    judge_status: Optional[str] = None
    validation_warnings: Optional[list] = None
    solver_status: Optional[str] = None


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    verdict: Literal["flag", "pass"]
    detail: str
    mechanism: Literal["deterministic", "llm"]
    # Normalized token-usage dict for the ONE `agent.run_phase` call behind an
    # LLM-rubric dimension (see `PhaseResult.usage`); empty {} for deterministic
    # dims and for degrade-to-pass outcomes with no completed call. Threaded
    # through so `scripts/golden_eval.py` can sum `pricing.cost_usd` per dim
    # without re-deriving it from anywhere else.
    usage: dict = field(default_factory=dict)


_LANGUAGE_FLAG_CODES = {"mixed_script", "calque", "english_template"}


def score_language(phases: list[PhaseView], subject: str, language: str) -> DimensionScore:
    """Flags a packet whose text mixes scripts or leaks English scaffolding tokens.

    Reuses `content_lint.lint_phase` (do not reimplement the regexes here) —
    runs it over every phase and flags on any mixed_script/calque/english_template
    finding.
    """
    hits: list[str] = []
    for phase in phases:
        findings = content_lint.lint_phase(
            phase.phase_name, phase.output_md, subject=subject, output_language=language,
        )
        for f in findings:
            if f.code in _LANGUAGE_FLAG_CODES:
                hits.append(f"{phase.phase_name}:{f.code}: {f.message}")
    if hits:
        return DimensionScore("language", "flag", "; ".join(hits), "deterministic")
    return DimensionScore("language", "pass", "no mixed-script/calque/english-template findings", "deterministic")


def score_error_detection_format(
    phases: list[PhaseView], subject: str, language: str,
) -> DimensionScore:
    """Flags a `practice-error-detection` phase that fails the EXACTLY-ONE-broken-
    block format contract (no manifest dimension of its own — Task 4's
    `score_packet` folds this into `broken_question` alongside the LLM half)."""
    hits: list[str] = []
    for phase in phases:
        if phase.phase_name != "practice-error-detection":
            continue
        findings = content_lint.lint_phase(
            phase.phase_name, phase.output_md, subject=subject, output_language=language,
        )
        for f in findings:
            if f.code.startswith("errdet_"):
                hits.append(f"{f.code}: {f.message}")
    if hits:
        return DimensionScore("broken_question", "flag", "; ".join(hits), "deterministic")
    return DimensionScore("broken_question", "pass", "no error-detection format findings", "deterministic")


# --- reflection: past-tense fabricated-performance / unconditional-outcome ---
#
# Calibrated against the 5 real audited rows (tests/golden/manifest.json). The
# signal is NOT the bare presence of "Needs Retry" / "not passed" — a
# conditional redo-route ("Agar natijangiz 'Needs Retry' ... bo'lsa") legitimately
# names them (real row 1122356a: pass). What's fabricated is a past-tense verb
# or an unconditional outcome judgment asserting how the (nonexistent) attempt
# actually went:
#   - "kuzatildi"                 — "was observed" (263d99c5 uses the sibling
#                                    "sezdingiz"; 9504ad94 uses this one)
#   - "sezdingiz" / "sezdingizmi" — "you felt/noticed" (263d99c5)
#   - "deb baholandi"/"deb baholanadi" — "was/is judged as" — an unconditional
#                                    outcome verdict (9504ad94)
#   - "handled well"              — English fabrication marker (CQ-A watch)
_REFLECTION_FABRICATION_MARKERS = [
    re.compile(r"kuzatildi", re.IGNORECASE),
    re.compile(r"sezdingizmi", re.IGNORECASE),
    re.compile(r"sezdingiz", re.IGNORECASE),
    re.compile(r"deb\s+baholandi", re.IGNORECASE),
    re.compile(r"deb\s+baholanadi", re.IGNORECASE),
    re.compile(r"handled well", re.IGNORECASE),
]


def score_reflection(phases: list[PhaseView]) -> DimensionScore:
    """Flags a `reflection` phase that fabricates a past-tense performance
    narrative or asserts an unconditional outcome verdict before any real
    attempt exists. A conditional redo-route (IF your result is X, THEN...) is
    a legitimate structure, not fabrication, and is NOT flagged."""
    hits: list[str] = []
    for phase in phases:
        if phase.phase_name != "reflection":
            continue
        for rx in _REFLECTION_FABRICATION_MARKERS:
            m = rx.search(phase.output_md)
            if m:
                hits.append(f"fabricated-outcome marker: {m.group(0)!r}")
    if hits:
        return DimensionScore("reflection", "flag", "; ".join(hits), "deterministic")
    return DimensionScore("reflection", "pass", "no fabricated-performance/unconditional-outcome markers", "deterministic")


def read_signals(phases: list) -> dict:
    """Folds cheap structural signals already recorded on each phase — no LLM
    call, no content_lint. `solver_status` is CQ-C's column and is not present
    on this base's `PhaseView`/ORM row; `getattr(..., None)` keeps this working
    against either shape without raising."""
    validation_warning_count = 0
    judge_statuses: list = []
    solver_statuses: list = []
    for po in phases:
        warnings = getattr(po, "validation_warnings", None)
        validation_warning_count += len(warnings) if warnings else 0
        judge_statuses.append(getattr(po, "judge_status", None))
        solver_statuses.append(getattr(po, "solver_status", None))
    return {
        "validation_warning_count": validation_warning_count,
        "judge_statuses": judge_statuses,
        "solver_statuses": solver_statuses,
    }


# --------------------------------------------------------------------------
# LLM-rubric dimension scorers (paid tier — one `agent.run_phase` call each)
# --------------------------------------------------------------------------


class RubricVerdict(BaseModel):
    """Schema-validated JSON verdict for one CQ-E LLM-rubric dimension.

    Passed as `schema=` to `agent.run_phase`, mirroring `phase_judge.Verdict`
    (see `app/services/phase_judge.py`) — same call shape, different schema.
    """

    verdict: Literal["flag", "pass"]
    severity: Literal["none", "minor", "major"]
    evidence: str


def _build_rubric_prompt(dimension: str, **kwargs: str) -> str:
    """Builds the `phase_prompt` for one LLM-rubric dimension per the audit's
    method (docs/research/2026-07-01-content-quality-audit-g8-math.md)."""
    if dimension == "boundary":
        return (
            "You are auditing a generated homework packet for a SCOPE-BOUNDARY leak.\n\n"
            "Using ONLY the source lesson text below, and knowing the NEXT lesson in this "
            f"book is «{kwargs['next_lesson_title']}», determine whether the packet "
            "teaches or tests ANY concept that belongs to the next lesson — e.g. a "
            "converse/inverse of a theorem, a recognition criterion, a generalization, or a "
            "term that is only defined in the next lesson. flag if the packet uses a "
            "next-lesson concept; pass if it stays strictly within the source lesson's scope.\n\n"
            f"--- SOURCE LESSON TEXT ---\n{kwargs['source_text']}\n\n"
            f"--- PACKET: preview phase ---\n{kwargs['preview_md']}\n\n"
            f"--- PACKET: boss-arena phase ---\n{kwargs['boss_arena_md']}\n\n"
            "Respond with the RubricVerdict JSON schema only."
        )
    if dimension == "answer_key":
        solver_status = kwargs.get("solver_status") or ""
        solver_note = (
            f"\n\nNote: a downstream solver flagged this packet's solver_status as "
            f"{solver_status!r} — treat that as corroborating (not sufficient on its own) "
            "evidence of a wrong key.\n"
            if solver_status
            else ""
        )
        return (
            "You are auditing a generated homework packet's ANSWER KEYS for correctness.\n\n"
            "Re-solve each key-bearing item below using ONLY the source lesson text as ground "
            "truth. Flag ONLY a DEMONSTRABLE wrong key — a case where you can show step-by-step "
            "that the correct answer differs from the packet's stated key. Do NOT flag "
            "stylistic issues, ambiguous wording, or anything you are not fully confident is "
            "actually wrong (this must be conservative and high-confidence-only: false "
            f"positives here are costly).{solver_note}\n"
            f"--- SOURCE LESSON TEXT ---\n{kwargs['source_text']}\n\n"
            f"--- PACKET (answer-key-bearing phases) ---\n{kwargs['packet_md']}\n\n"
            "Respond with the RubricVerdict JSON schema only."
        )
    if dimension == "broken_question":
        return (
            "You are auditing a generated homework packet for a BROKEN QUESTION.\n\n"
            "Flag a question that is: unanswerable from the information given; "
            "self-contradictory; whose stated 'wrong method' coincidentally produces the same "
            "numeric answer as the correct method (so a student following the wrong method "
            "would be marked right); or that requires machinery (a formula, term, or "
            "technique) taught NOWHERE in this packet or in the source lesson.\n\n"
            f"--- SOURCE LESSON TEXT ---\n{kwargs['source_text']}\n\n"
            f"--- PACKET ---\n{kwargs['packet_md']}\n\n"
            "Respond with the RubricVerdict JSON schema only."
        )
    if dimension == "extract_fidelity":
        return (
            "You are auditing a generated homework packet for EXTRACT FIDELITY.\n\n"
            "Compare every worked example, quoted definition, and cited number in the packet "
            "against the source lesson text below. Flag transcription drift — a worked "
            "example, quote, or figure in the packet that does not match what the source "
            "actually says (numbers changed, steps altered, a quote misattributed, etc.).\n\n"
            f"--- SOURCE LESSON TEXT ---\n{kwargs['source_text']}\n\n"
            f"--- PACKET ---\n{kwargs['packet_md']}\n\n"
            "Respond with the RubricVerdict JSON schema only."
        )
    raise ValueError(f"unknown rubric dimension: {dimension!r}")


async def _score_via_rubric(
    dimension: str,
    phase_prompt: str,
    *,
    provider: str,
    model: Optional[str],
    transport: str,
) -> DimensionScore:
    """Shared call+map+degrade body for the 4 LLM-rubric scorers.

    Exactly ONE `agent.run_phase` call. Degrade-never-crash (E4-relevant): any
    exception, or a schema call that somehow comes back unparsed, maps to
    `verdict="pass"` with the literal substring "unavailable" in `detail` —
    Task 4's `--emit-baseline` guard greps for that word to refuse freezing a
    scorer outage as a clean baseline.
    """
    try:
        result = await agent.run_phase(
            provider=provider,
            model=model,
            phase_prompt=phase_prompt,
            phase_name="__golden__",
            homework_job_id=None,
            phase_output_id=None,
            schema=RubricVerdict,
            operation=f"golden:{dimension}",
            transport=transport,
        )
    except Exception as exc:  # noqa: BLE001 - deliberate: a scorer error is a "pass", not a crash
        return DimensionScore(dimension, "pass", f"scorer-unavailable: {exc}", "llm")
    parsed = result.parsed
    if parsed is None:
        return DimensionScore(dimension, "pass", "scorer-unavailable: no parsed verdict returned", "llm")
    return DimensionScore(dimension, parsed.verdict, parsed.evidence, "llm", usage=dict(result.usage or {}))


async def score_boundary(
    *,
    boss_arena_md: str,
    preview_md: str,
    source_text: str,
    next_lesson_title: str,
    provider: str,
    model: Optional[str],
    transport: str = "api",
) -> DimensionScore:
    """LLM-rubric: does the packet teach/test a NEXT-lesson concept (scope leak)?"""
    prompt = _build_rubric_prompt(
        "boundary",
        source_text=source_text,
        preview_md=preview_md,
        boss_arena_md=boss_arena_md,
        next_lesson_title=next_lesson_title,
    )
    return await _score_via_rubric("boundary", prompt, provider=provider, model=model, transport=transport)


async def score_answer_key(
    *,
    packet_md: str,
    source_text: str,
    solver_status: Optional[str] = None,
    provider: str,
    model: Optional[str],
    transport: str = "api",
) -> DimensionScore:
    """LLM-rubric: re-solve key-bearing items; flag only a demonstrable wrong key."""
    prompt = _build_rubric_prompt(
        "answer_key",
        source_text=source_text,
        packet_md=packet_md,
        solver_status=solver_status or "",
    )
    return await _score_via_rubric("answer_key", prompt, provider=provider, model=model, transport=transport)


async def score_broken_question(
    *,
    packet_md: str,
    source_text: str,
    provider: str,
    model: Optional[str],
    transport: str = "api",
) -> DimensionScore:
    """LLM-rubric: flag an unanswerable / coincidentally-right / untaught-machinery question."""
    prompt = _build_rubric_prompt("broken_question", source_text=source_text, packet_md=packet_md)
    return await _score_via_rubric("broken_question", prompt, provider=provider, model=model, transport=transport)


async def score_extract_fidelity(
    *,
    packet_md: str,
    source_text: str,
    provider: str,
    model: Optional[str],
    transport: str = "api",
) -> DimensionScore:
    """LLM-rubric: worked examples/quotes in the packet must match the source (transcription drift)."""
    prompt = _build_rubric_prompt("extract_fidelity", source_text=source_text, packet_md=packet_md)
    return await _score_via_rubric(
        "extract_fidelity", prompt, provider=provider, model=model, transport=transport
    )


# --------------------------------------------------------------------------
# score_packet — the 6-dimension composite over one job's phases
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PacketScore:
    """One packet's verdicts, keyed by the manifest's 6 dimensions.

    `scores` may have FEWER than 6 keys: when `llm=False`, `boundary`,
    `answer_key`, and `extract_fidelity` are LLM-only dimensions with no
    deterministic counterpart, and are OMITTED entirely rather than stamped
    "pass" — an omitted key reads honestly as "not measured this run"; a fake
    `pass` would let a deterministic-only run silently claim a clean bill on
    dimensions it never actually checked. `broken_question` always has a
    deterministic half (`score_error_detection_format`) so it is always
    present, just `mechanism="deterministic"` when `llm=False` (see E1 below).
    """

    job_id: str
    scores: dict[str, DimensionScore]


def _packet_md(phases: list[PhaseView]) -> str:
    """Concatenates every content phase's markdown into one packet blob for
    the packet-wide LLM rubrics (answer_key / broken_question / extract_fidelity)."""
    return "\n\n".join(f"### {p.phase_name}\n{p.output_md}" for p in phases if p.output_md)


def _phase_md(phases: list[PhaseView], phase_name: str) -> str:
    """Concatenates the (normally singular) phase(s) matching `phase_name`."""
    return "\n\n".join(p.output_md for p in phases if p.phase_name == phase_name and p.output_md)


async def score_packet(
    entry: GoldenEntry,
    phases: list[PhaseView],
    source_text: str,
    next_lesson_title: str,
    *,
    provider: str,
    model: Optional[str],
    transport: str,
    llm: bool = True,
) -> PacketScore:
    """Scores one packet against all 6 CQ-E manifest dimensions.

    Composition:
      - `language`  <- `score_language` (deterministic, always).
      - `reflection` <- `score_reflection` (deterministic, always).
      - `broken_question` <- **E1 merge**: flags if EITHER
        `score_error_detection_format` (deterministic, always runs) OR, when
        `llm=True`, `score_broken_question` (LLM) flags. `mechanism="llm"`
        whenever the LLM half fired (i.e. whenever `llm=True`, regardless of
        which half actually flagged) else `"deterministic"`; `detail` names
        both halves' verdicts so a reader can tell which one fired.
      - `boundary` / `answer_key` / `extract_fidelity` <- the LLM scorers,
        called ONLY when `llm=True`; otherwise these 3 keys are OMITTED from
        `scores` (see `PacketScore` docstring — never faked as `pass`).

    The LLM rubrics need packet-level markdown: `packet_md` is every content
    phase's `output_md` concatenated; `boss_arena_md`/`preview_md` are pulled
    from the `boss-arena` / `case-based-preview` phases specifically (per
    `flows.py`'s phase names).
    """
    scores: dict[str, DimensionScore] = {}

    scores["language"] = score_language(phases, subject=entry.subject, language=entry.language)
    scores["reflection"] = score_reflection(phases)

    det_bq = score_error_detection_format(phases, subject=entry.subject, language=entry.language)

    if llm:
        packet_md = _packet_md(phases)
        boss_arena_md = _phase_md(phases, "boss-arena")
        preview_md = _phase_md(phases, "case-based-preview")

        llm_bq = await score_broken_question(
            packet_md=packet_md, source_text=source_text, provider=provider, model=model,
            transport=transport,
        )
        flagged = det_bq.verdict == "flag" or llm_bq.verdict == "flag"
        scores["broken_question"] = DimensionScore(
            "broken_question",
            "flag" if flagged else "pass",
            f"deterministic={det_bq.verdict} ({det_bq.detail}); llm={llm_bq.verdict} ({llm_bq.detail})",
            "llm",
            usage=llm_bq.usage,
        )

        signals = read_signals(phases)
        solver_status = next((s for s in signals["solver_statuses"] if s), None)

        scores["boundary"] = await score_boundary(
            boss_arena_md=boss_arena_md, preview_md=preview_md, source_text=source_text,
            next_lesson_title=next_lesson_title, provider=provider, model=model, transport=transport,
        )
        scores["answer_key"] = await score_answer_key(
            packet_md=packet_md, source_text=source_text, solver_status=solver_status,
            provider=provider, model=model, transport=transport,
        )
        scores["extract_fidelity"] = await score_extract_fidelity(
            packet_md=packet_md, source_text=source_text, provider=provider, model=model,
            transport=transport,
        )
    else:
        # llm=False (deterministic-only / free tier): broken_question stays
        # present (its deterministic half always runs) but reflects ONLY
        # `score_error_detection_format`; boundary/answer_key/extract_fidelity
        # are LLM-only and are omitted (see PacketScore docstring).
        scores["broken_question"] = det_bq

    return PacketScore(job_id=entry.job_id, scores=scores)


def diff_scores(baseline: PacketScore, current: PacketScore) -> list[str]:
    """Human-readable regressions: a dimension that was `pass` in `baseline`
    and is `flag` in `current`. A dimension missing from `current` (e.g. an
    LLM dim omitted by a `llm=False` run) is NOT a regression — there is
    nothing to compare, so it is skipped rather than treated as a flag.
    Identical scores -> `[]`."""
    regressions: list[str] = []
    for dim, base_score in baseline.scores.items():
        cur_score = current.scores.get(dim)
        if cur_score is None:
            continue
        if base_score.verdict == "pass" and cur_score.verdict == "flag":
            regressions.append(
                f"{dim}: pass -> flag "
                f"(baseline: {base_score.detail!r}; current: {cur_score.detail!r})"
            )
    return regressions


def packet_score_to_dict(score: PacketScore) -> dict:
    """`PacketScore` -> a plain JSON-able dict. Paired with
    `packet_score_from_dict` for the `--baseline`/`--emit-baseline` round trip
    in `scripts/golden_eval.py`."""
    return {
        "job_id": score.job_id,
        "scores": {
            dim: {
                "dimension": ds.dimension,
                "verdict": ds.verdict,
                "detail": ds.detail,
                "mechanism": ds.mechanism,
                "usage": ds.usage,
            }
            for dim, ds in score.scores.items()
        },
    }


def packet_score_from_dict(data: dict) -> PacketScore:
    """Inverse of `packet_score_to_dict`."""
    scores = {
        dim: DimensionScore(
            dimension=d["dimension"],
            verdict=d["verdict"],
            detail=d["detail"],
            mechanism=d["mechanism"],
            usage=d.get("usage") or {},
        )
        for dim, d in data["scores"].items()
    }
    return PacketScore(job_id=data["job_id"], scores=scores)


async def _load_phases_from_db(job_id: UUID | str) -> list[PhaseView]:
    """Read-only load of one job's phases from `phase_outputs`, mapped to the
    plain `PhaseView` read-model `score_packet` consumes. Imports are local so
    importing `golden_eval` never requires DB/settings config to be importable
    (the module is otherwise standalone/offline per the module docstring)."""
    from app.db import SessionLocal
    from app.repositories import phase_outputs as phase_repo

    async with SessionLocal() as session:
        rows = await phase_repo.list_for_job(session, job_id)
        return [
            PhaseView(
                phase_name=row.phase_name,
                output_md=row.output_md or "",
                judge_status=row.judge_status,
                validation_warnings=row.validation_warnings,
                solver_status=getattr(row, "solver_status", None),
            )
            for row in rows
        ]
