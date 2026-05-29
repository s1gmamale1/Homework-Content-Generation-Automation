"""Build the Flow v2 SourceMap from extracted lesson content (Phase 2).

The SourceMap is produced ONLY from the already-extracted lesson text — never
from a fresh PDF read and never from model world-knowledge. It is the factual
anchor downstream phases cite by concept id. Kept deliberately small and cheap:
it runs on the pinned extract tier (``settings.extract_provider`` / ``model``),
like the extract phase itself.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from app.schemas.flow_v2 import SourceMap
from app.services import agent

_SOURCE_MAP_INSTRUCTION = (
    "You are building a SOURCE MAP: the factual anchor for one homework lesson.\n"
    "Using ONLY the lesson content provided above (already extracted faithfully "
    "from the textbook), produce structured JSON capturing exactly what the "
    "textbook teaches in this section — nothing more.\n\n"
    "Requirements:\n"
    "- core_concepts: every distinct concept the section teaches. Give each a "
    "STABLE, unique short id ('c1','c2','c3',...), a short name, and a one- to "
    "two-sentence factual summary drawn from the lesson. Downstream homework "
    "phases reference these ids, so they must be stable and unique.\n"
    "- main_rules_or_formulas, key_terms, textbook_examples, common_mistakes, "
    "required_skills: fill from the lesson content. 'required_skills' are the "
    "concrete things a student must be able to DO after this section.\n"
    "- NO INVENTION: every item must trace back to the provided lesson content. "
    "Do NOT add facts, formulas, dates, definitions, or examples that are not "
    "present in the source. If the lesson is thin, return fewer items rather "
    "than inventing. Leave a list empty if the source offers nothing for it.\n"
    "- Preserve the source language (Uzbek stays Uzbek, Russian stays Russian).\n"
    "- source_alignment_note: one sentence on how this map reflects the source.\n"
    "- forbidden_invention_affirmation: affirm that nothing was invented beyond "
    "the provided lesson content."
)


async def build_source_map(
    *,
    provider: str,
    model: Optional[str],
    subject: str,
    grade: Optional[int],
    language: str,
    section_number: str,
    section_title: str,
    page_range: str,
    textbook_title: str,
    lesson_context: str,
    homework_job_id: UUID,
    phase_output_id: Optional[UUID] = None,
) -> SourceMap:
    """Build + return a SourceMap for one section.

    Raises on hard failure (e.g. the model never returns valid JSON); callers in
    the pipeline wrap this so a failure never breaks the generation flow.
    """
    parsed, _pt, _ot = await agent.run_phase_prompt_structured(
        provider=provider,
        model=model,
        phase_prompt=_SOURCE_MAP_INSTRUCTION,
        response_schema=SourceMap,
        lesson_context=lesson_context,
        prior_outputs={},
        difficulty=None,
        phase_name="source-map",
        homework_job_id=homework_job_id,
        phase_output_id=phase_output_id,
    )
    assert isinstance(parsed, SourceMap)
    sm = parsed

    # Back-fill addressing/provenance from what we already know — don't trust the
    # model to echo metadata correctly (it only sees lesson text, not the job).
    sm.subject = subject or sm.subject
    sm.grade = grade if grade is not None else sm.grade
    sm.language = language or sm.language
    sm.section_number = section_number or sm.section_number
    sm.section_title = section_title or sm.section_title
    sm.page_range = page_range or sm.page_range
    sm.textbook_title = textbook_title or sm.textbook_title

    # Guarantee stable, unique, non-empty concept ids (c1..cN) regardless of what
    # the model returned. Downstream phases depend on this invariant.
    seen: set[str] = set()
    for i, concept in enumerate(sm.core_concepts, start=1):
        cid = (concept.id or "").strip()
        if not cid or cid in seen:
            cid = f"c{i}"
        concept.id = cid
        seen.add(cid)

    logger.success(
        "source_map built | section={} concepts={} terms={} rules={}",
        section_number or "?",
        len(sm.core_concepts),
        len(sm.key_terms),
        len(sm.main_rules_or_formulas),
    )
    return sm
