"""Plan §10 source fidelity: a content phase must only cite concept IDs that
exist in the job's SourceMap. These tests pin the pure validator that detects
invented (hallucinated) IDs so the pipeline can surface them.
"""

from app.services.pipeline import _emitted_concept_ids, _unknown_concept_ids


class _Obj:
    """Minimal stand-in for a parsed structured phase (getattr-based)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_flags_invented_top_level_concept_ids():
    parsed = _Obj(concept_ids=["c1", "c2", "ghost"])
    assert _unknown_concept_ids(parsed, {"c1", "c2"}) == ["ghost"]


def test_checks_source_concept_ids_field_too():
    # CaseBasedPreview / CbpModeGame use `source_concept_ids`.
    parsed = _Obj(source_concept_ids=["c1", "x9"])
    assert _unknown_concept_ids(parsed, {"c1"}) == ["x9"]


def test_checks_nested_boss_question_concept_ids():
    # BossArena carries concept_ids per question, not top-level.
    parsed = _Obj(questions=[_Obj(concept_ids=["c1"]), _Obj(concept_ids=["bad"])])
    assert _unknown_concept_ids(parsed, {"c1", "c2"}) == ["bad"]


def test_empty_source_map_skips_validation():
    # No map => can't validate => never flag (avoids false "all invented").
    parsed = _Obj(concept_ids=["anything"])
    assert _unknown_concept_ids(parsed, set()) == []


def test_all_grounded_returns_empty():
    parsed = _Obj(concept_ids=["c1", "c2"])
    assert _unknown_concept_ids(parsed, {"c1", "c2", "c3"}) == []


def test_unknown_ids_are_deduplicated_and_order_stable():
    parsed = _Obj(concept_ids=["bad", "c1", "bad", "also"])
    assert _unknown_concept_ids(parsed, {"c1"}) == ["bad", "also"]


def test_emitted_collects_all_locations():
    parsed = _Obj(
        concept_ids=["a"],
        source_concept_ids=["b"],
        questions=[_Obj(concept_ids=["c"])],
    )
    assert set(_emitted_concept_ids(parsed)) == {"a", "b", "c"}
