"""Flow v2 SourceMap — the factual anchor for one generated homework section.

Built once per job, immediately after the lesson text is extracted from the
textbook. Every downstream phase and game references these concepts *by id*, so
generated content stays grounded in the source (Flow v2 "source fidelity" /
"no invention" rule). The extraction shape mirrors the Case-Based Preview
standard §4 (topic / core_concept / main_rule / key_terms / textbook_examples /
common_mistake / student_must_be_able_to), promoted to first-class fields with
stable concept ids.

See docs/nets_pure_content_automation_flow_v2_plan.md (Phase 2).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SourceConcept(BaseModel):
    """A single concept the textbook actually teaches in this section.

    `id` is a STABLE short token ("c1", "c2", …). Downstream phases cite it via
    `source_concept_ids`, so it must be unique within a SourceMap and must not be
    reassigned once content has been generated against it.
    """

    id: str = Field(
        description="Stable short id, e.g. 'c1','c2'. Unique within this map; "
        "downstream phases reference concepts by this id.",
    )
    name: str = Field(description="Short concept name, in the source language.")
    summary: str = Field(
        default="",
        description="One- or two-sentence factual summary taken from the "
        "textbook. No elaboration beyond the source.",
    )


class SourceMap(BaseModel):
    """Structured, source-faithful map of one textbook section.

    This is the keystone of Flow v2: it is produced ONLY from extracted lesson
    text, never from model world-knowledge, and is the single object every later
    phase consults to decide what may legitimately appear in the homework.
    """

    # ── provenance / addressing (back-filled by the builder, not the model) ──
    subject: str = ""
    grade: Optional[int] = None
    language: str = "uz"
    textbook_title: str = ""
    section_number: str = ""
    section_title: str = ""
    page_range: str = ""

    # ── the extracted substance (the model fills these from the lesson) ──────
    core_concepts: list[SourceConcept] = Field(
        default_factory=list,
        description="Each distinct concept the section teaches. Every entry MUST "
        "have a stable, unique id.",
    )
    main_rules_or_formulas: list[str] = Field(
        default_factory=list,
        description="Rules, laws, theorems, or formulas stated in the section.",
    )
    key_terms: list[str] = Field(
        default_factory=list,
        description="Vocabulary/terminology the section defines or relies on.",
    )
    textbook_examples: list[str] = Field(
        default_factory=list,
        description="Worked examples / illustrations present in the section.",
    )
    common_mistakes: list[str] = Field(
        default_factory=list,
        description="Misconceptions or errors the section warns about (only if "
        "the source mentions them).",
    )
    required_skills: list[str] = Field(
        default_factory=list,
        description="Concrete things the student must be able to DO after this "
        "section (action-oriented).",
    )

    # ── self-attestation of source fidelity ──────────────────────────────────
    source_alignment_note: str = Field(
        default="",
        description="Brief note on how this map reflects the source section.",
    )
    forbidden_invention_affirmation: str = Field(
        default="",
        description="Explicit affirmation that nothing here was invented beyond "
        "the provided lesson content.",
    )
