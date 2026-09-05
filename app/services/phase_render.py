"""Deterministic markdown renderers for structured phases.

Markdown is DERIVED from content_json, never authored. Its shape must stay close
enough to the previous hand-authored markdown that the judge, solver,
content_lint, teaching audit, Notion renderer and the operator console keep
working — that is the renderer's real contract, verified in tests.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.content_json import RlcConfig, SentenceFillConfig

RENDERER_VERSION = "2"

# Author-only heading. MUST match the platform's redactor so the whole section is
# stripped before a student sees it: `_SECTION_MARKERS` contains "answer key", and
# `strip_answer_sections` skips from the heading until the next heading of equal or
# higher level (ours is last, so it swallows to EOF).
#
# It has to be here at all because practice-rlc is in pipeline._SOLVER_PHASES: the
# answer-key solver re-checks the key in the MARKDOWN, and the judge grades against
# a prompt contract that says "Mark which option is correct". A renderer that
# omitted the key would silently break both.
_ANSWER_HEADING = "## Answer key"

# Evaluation sees render_md's projection, not the JSON authoring transport.
# Keep the mapping beside the renderers so changes to their shape are reflected
# in both judge and solver contracts without duplicating the semantic policy.
REVIEW_PROJECTIONS = {
    "practice-rlc": (
        "The title is an H1, followed by intro and **Role:** expert_role. "
        "The five ordered steps are H2 headings '<number>. <title>': decision, "
        "info_request, final_decision, concept_select, reasoning. Each includes "
        "its prompt; options/chips are untagged bullet labels. The reasoning "
        "minimum is rendered as '_Minimum N characters._'. Correct option/chip "
        "labels appear by step number in the terminal Answer key; reasoning is "
        "an open response with its minimum, not a unique answer. IDs, kind fields "
        "and boolean correctness flags are represented by this ordering/mapping, "
        "not printed as JSON. No prediction, per-choice feedback, localized role "
        "metadata, kind-tagged headings or final summary is supported or required."
    ),
    "practice-sentence": (
        "The fixed H1 is 'Sentence fill'. Each numbered H2 contains the passage "
        "with ___ blanks and a **Word bank:** line listing its entries. The "
        "terminal Answer key lists each item's answers in blank order. IDs and "
        "the word_bank mode field are not printed. No wrong-bank explanatory "
        "feedback or reflection field is supported or required."
    ),
}


class RenderError(RuntimeError):
    """No renderer registered for this phase, or the config is the wrong type."""


def _render_rlc(cfg: RlcConfig) -> str:
    out: list[str] = [f"# {cfg.title}", "", cfg.intro, "",
                      f"**Role:** {cfg.expert_role}", ""]
    for n, step in enumerate(cfg.steps, start=1):
        out += [f"## {n}. {step.title}", "", step.prompt, ""]
        for choice in (step.options or []):
            out.append(f"- {choice.label}")
        for chip in (step.concept_chips or []):
            out.append(f"- {chip.label}")
        if step.kind == "reasoning":
            out.append(f"_Minimum {step.min_chars} characters._")
        out.append("")
    out += [_ANSWER_HEADING, ""]
    for n, step in enumerate(cfg.steps, start=1):
        picks = [c.label for c in (step.options or []) if c.is_correct]
        picks += [c.label for c in (step.concept_chips or []) if c.is_correct]
        if picks:
            out.append(f"{n}. {picks[0]}")
        elif step.kind == "reasoning":
            out.append(f"{n}. (open response, minimum {step.min_chars} characters)")
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_sentence(cfg: SentenceFillConfig) -> str:
    out: list[str] = ["# Sentence fill", ""]
    for n, item in enumerate(cfg.items, start=1):
        out += [f"## {n}.", "", item.passage, "",
                "**Word bank:** " + ", ".join(item.word_bank), ""]
    out += [_ANSWER_HEADING, ""]
    for n, item in enumerate(cfg.items, start=1):
        out.append(f"{n}. " + ", ".join(item.answers))
    out.append("")
    return "\n".join(out).rstrip() + "\n"


_RENDERERS = {
    "practice-rlc": (RlcConfig, _render_rlc),
    "practice-sentence": (SentenceFillConfig, _render_sentence),
}


def render_md(phase_name: str, cfg: BaseModel) -> str:
    entry = _RENDERERS.get(phase_name)
    if entry is None:
        raise RenderError(f"no renderer for phase '{phase_name}'")
    expected_type, fn = entry
    if not isinstance(cfg, expected_type):
        raise RenderError(
            f"phase '{phase_name}' expects {expected_type.__name__}, got {type(cfg).__name__}"
        )
    return fn(cfg)
