"""The single artifact every generation path returns.

Judge regen and solver regen both replace output_md wholesale. If content_json
were persisted independently, a regenerated markdown would survive beside a
stale JSON and the "source of truth" would be a lie. So every path — initial,
judge regen, solver regen, markdown fallback — returns one of these, and it is
persisted only after the final accepted generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

from app.models.phase_output import AUTHORING_MODES
from app.services.phase_render import RENDERER_VERSION, REVIEW_PROJECTIONS, RenderError, render_md
from app.services.prompts import get_structured_prompt

_MARKDOWN_MODES = tuple(m for m in AUTHORING_MODES if m != "structured")


class StructuredPhaseError(RuntimeError):
    """A schema-validation or render-conformance failure.

    Deliberately distinct from transport errors: the pipeline falls back to
    markdown ONLY on this type. Auth, 429, slot-saturation, timeout and network
    errors must keep their existing retry/failover semantics.
    """


@dataclass(frozen=True)
class PhaseArtifact:
    output_md: str
    content_json: Optional[dict] = None
    authoring_mode: str = "markdown_legacy"
    content_schema_version: Optional[str] = None
    renderer_version: Optional[str] = None


def artifact_from_config(phase_name: str, cfg: BaseModel) -> PhaseArtifact:
    """Render markdown from a validated config and pair them atomically."""
    try:
        md = render_md(phase_name, cfg)
    except RenderError as exc:
        raise StructuredPhaseError(str(exc)) from exc
    if not md.strip():
        raise StructuredPhaseError(f"renderer produced empty markdown for '{phase_name}'")
    return PhaseArtifact(
        output_md=md,
        content_json=cfg.model_dump(mode="json"),
        authoring_mode="structured",
        content_schema_version=getattr(cfg, "SCHEMA_VERSION", None),
        renderer_version=RENDERER_VERSION,
    )


def artifact_from_markdown(output_md: str, *, mode: str) -> PhaseArtifact:
    if mode not in _MARKDOWN_MODES:
        raise ValueError(f"'{mode}' is not a markdown authoring mode")
    return PhaseArtifact(output_md=output_md, authoring_mode=mode)


def review_contract(
    artifact: PhaseArtifact, *, subject: str, phase_name: str,
    output_language: str, custom_prompt: str | None = None,
) -> str | None:
    """Resolve from the current artifact on every review, including repairs.

    None retains the reviewers' built-in Markdown resolver. A custom prompt
    disables structured authoring; fallback artifacts likewise use Markdown.
    """
    if artifact.authoring_mode != "structured":
        return custom_prompt
    projection = REVIEW_PROJECTIONS[phase_name]
    contract = get_structured_prompt(subject, phase_name, output_language=output_language)
    if contract is None:
        raise ValueError(f"no structured review contract for {phase_name}")
    return (
        "## Representation used for this review\n"
        "OUTPUT is a deterministic Markdown projection of validated structured JSON. "
        "The representation mapping here governs how to apply the structured "
        "authoring contract below: JSON-only transport and schema field syntax "
        "apply to the source JSON, not to this Markdown. Do not demand JSON or "
        "the ordinary hand-authored Markdown phase's extra sections. "
        + projection + "\n"
        "The terminal Answer key is author-only and not student-visible evidence. "
        "Its correctness still must be independently checked. The fixed renderer labels "
        "(Role, Minimum, characters, Sentence fill, Word bank, Answer key, open "
        "response) and expert_role enum are renderer chrome; apply the language "
        "rules to all authored titles, intros, prompts, passages and choice/bank "
        "text. This mapping changes representation only: enforce all applicable "
        "shared learner semantics, grade/language rules, visible evidence, "
        "lesson fidelity, item counts and answer correctness below.\n\n"
        "## Structured authoring contract (apply through the mapping above)\n"
        + contract
    )
