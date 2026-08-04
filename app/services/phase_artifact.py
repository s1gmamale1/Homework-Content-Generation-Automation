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
from app.services.phase_render import RENDERER_VERSION, RenderError, render_md

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
