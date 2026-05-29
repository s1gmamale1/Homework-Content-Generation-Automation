"""SourceMap threading into phase prompts (plan §10 — source fidelity).

The plan's load-bearing content rule is "every phase/game traces to source-map
concept IDs; no invented facts." PR-1 persists the SourceMap; these tests pin
that it's now THREADED into each content phase's prompt as the authoritative
concept list (ids for grounding only, never printed in student-facing text).
"""

from __future__ import annotations

from app.services.agent import _build_master_prompt, format_source_map_digest

_SM = {
    "subject_family": "math-algebra",
    "chapter": "Linear equations",
    "section": "Solving one-variable equations",
    "concepts": [
        {"id": "isolate-variable", "label": "Isolate the variable",
         "statement": "Move variable terms to one side, constants to the other."},
        {"id": "inverse-operation", "label": "Inverse operations",
         "statement": "Subtract to undo addition; divide to undo multiplication."},
    ],
}


def test_digest_lists_every_concept_id_label_statement() -> None:
    d = format_source_map_digest(_SM)
    for c in _SM["concepts"]:
        assert c["id"] in d
        assert c["label"] in d
        assert c["statement"] in d


def test_digest_states_authority_and_grounding_only_rule() -> None:
    d = format_source_map_digest(_SM).lower()
    # "cover these / invent nothing" authority framing + "ids grounding only".
    assert "invent" in d
    assert "grounding" in d or "never print" in d


def test_digest_empty_for_none_or_no_concepts() -> None:
    assert format_source_map_digest(None) == ""
    assert format_source_map_digest({}) == ""
    assert format_source_map_digest({"concepts": []}) == ""


def test_master_prompt_includes_digest_when_provided() -> None:
    digest = format_source_map_digest(_SM)
    prompt = _build_master_prompt(
        phase_prompt="Make a game.",
        phase_name="practice-tictactoe",
        lesson_context="some lesson",
        prior_outputs=None,
        difficulty="hard",
        schema=None,
        provider_suffix="",
        source_map_digest=digest,
    )
    assert "isolate-variable" in prompt
    assert "inverse-operation" in prompt


def test_master_prompt_omits_digest_block_when_absent() -> None:
    # Default (no digest) keeps the prompt unchanged — no stray SOURCE MAP header.
    prompt = _build_master_prompt(
        phase_prompt="Make a game.",
        phase_name="practice-tictactoe",
        lesson_context="some lesson",
        prior_outputs=None,
        difficulty="hard",
        schema=None,
        provider_suffix="",
    )
    assert "SOURCE MAP" not in prompt
