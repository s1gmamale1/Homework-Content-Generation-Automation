"""Deterministic markdown renderers for structured phases.

Markdown is DERIVED from content_json, never authored. Its shape must stay close
enough to the previous hand-authored markdown that the judge, solver,
content_lint, teaching audit, Notion renderer and the operator console keep
working — that is the renderer's real contract, verified in tests.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.content_json import RlcConfig, SentenceFillConfig

RENDERER_VERSION = "1"


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
    return "\n".join(out).rstrip() + "\n"


def _render_sentence(cfg: SentenceFillConfig) -> str:
    out: list[str] = ["# Sentence fill", ""]
    for n, item in enumerate(cfg.items, start=1):
        out += [f"## {n}.", "", item.passage, "",
                "**Word bank:** " + ", ".join(item.word_bank), ""]
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
