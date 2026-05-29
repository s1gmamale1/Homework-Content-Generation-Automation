"""Flow v2 canonical content schemas (pure content-automation plan).

Phase 2 introduces the SourceMap — the factual anchor every downstream
phase/game references by concept id. Further v2 schemas (case_based_preview,
practice_arc, boss_arena, …) land in later phases.
"""

from app.schemas.flow_v2.source_map import SourceConcept, SourceMap

__all__ = ["SourceMap", "SourceConcept"]
