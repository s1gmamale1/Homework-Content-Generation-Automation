"""Prove `prompt_set_id` actually threads through every contract reader and
every caller that resolves a built-in contract (judge, solver) -- not just
that the registry itself works (see test_prompt_sets.py for that).

Pure unit tests: a temporary two-set registry with deliberately DIFFERENT
content per set is the only way to prove routing (a single-set fixture can't
distinguish "routed correctly" from "ignored the kwarg and got lucky").
`agent.run_phase` is mocked at the same real boundary test_judge_contract_
override.py and test_solver.py already use, so this lands on judge()/solve()'s
actual prompt-building code, not a re-implementation of it.
"""
import json
import types

import pytest

from app.services import agent
from app.services import phase_judge as pj
from app.services import prompt_sets as PS
from app.services import prompts as P
from app.services import solver
from app.services.phase_judge import Verdict
from app.services.solver import SolveVerdict

_SET_A_SENTINEL = "SENTINEL-SET-A-CONTRACT"
_SET_B_SENTINEL = "SENTINEL-SET-B-CONTRACT"
_SET_A_STRUCTURED = "SENTINEL-SET-A-STRUCTURED JSON only."
_SET_B_STRUCTURED = "SENTINEL-SET-B-STRUCTURED JSON only."
_SET_A_FIDELITY = "SENTINEL-SET-A-FIDELITY plain text serialized."
_SET_B_FIDELITY = "SENTINEL-SET-B-FIDELITY plain text serialized."


def _write_set(root, general_sentinel: str, structured_sentinel: str, fidelity_sentinel: str):
    general = root / "_general"
    structured = general / "structured"
    structured.mkdir(parents=True)
    for name in PS.REQUIRED_PHASE_FILES:
        (general / name).write_text(
            f"{general_sentinel}\n\n{{{{SUBJECT}}}}\n\n{{{{LANGUAGE_RULES}}}}\n\n"
            f"{{{{FAMILY_RULES}}}}\n",
            encoding="utf-8",
        )
    for rel in ("structured/practice-rlc.md", "structured/practice-sentence.md",
                "structured/teacher-deck.md"):
        (general / rel).write_text(
            f"{structured_sentinel} {{{{SUBJECT}}}}\n", encoding="utf-8")
    (general / "structured" / "teacher-deck.fidelity.md").write_text(
        f"{fidelity_sentinel}\n", encoding="utf-8")


@pytest.fixture
def two_set_registry(tmp_path, monkeypatch):
    _write_set(tmp_path / "sets" / "set-a", _SET_A_SENTINEL, _SET_A_STRUCTURED, _SET_A_FIDELITY)
    _write_set(tmp_path / "sets" / "set-b", _SET_B_SENTINEL, _SET_B_STRUCTURED, _SET_B_FIDELITY)
    manifest = {
        "schema": "hcga-prompt-sets@1",
        "default": "set-a",
        "sets": [
            {"id": "set-a", "label": "Set A", "root": "sets/set-a", "description": "a"},
            {"id": "set-b", "label": "Set B", "root": "sets/set-b", "description": "b"},
        ],
    }
    manifest_path = tmp_path / "prompt-sets.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(PS, "MANIFEST_PATH", manifest_path)
    PS._reset_cache_for_tests()
    P._cache.clear()
    P._hash_cache.clear()
    P._teacher_deck_fidelity_cache.clear()
    yield
    PS._reset_cache_for_tests()
    P._cache.clear()
    P._hash_cache.clear()
    P._teacher_deck_fidelity_cache.clear()


# --- Markdown / structured / hash / fidelity readers all route on prompt_set_id --

def test_markdown_authoring_routes_on_prompt_set_id(two_set_registry):
    a = P.get_prompt("history", "flashcards", prompt_set_id="set-a")
    b = P.get_prompt("history", "flashcards", prompt_set_id="set-b")
    assert _SET_A_SENTINEL in a and _SET_B_SENTINEL not in a
    assert _SET_B_SENTINEL in b and _SET_A_SENTINEL not in b


def test_structured_authoring_routes_on_prompt_set_id(two_set_registry):
    a = P.get_structured_prompt("history", "practice-rlc", prompt_set_id="set-a")
    b = P.get_structured_prompt("history", "practice-rlc", prompt_set_id="set-b")
    assert _SET_A_STRUCTURED.split()[0] in a
    assert _SET_B_STRUCTURED.split()[0] in b
    assert a != b


def test_teacher_deck_fidelity_routes_on_prompt_set_id(two_set_registry):
    a = P.get_teacher_deck_fidelity_contract(prompt_set_id="set-a")
    b = P.get_teacher_deck_fidelity_contract(prompt_set_id="set-b")
    assert _SET_A_FIDELITY.strip() == a.strip()
    assert _SET_B_FIDELITY.strip() == b.strip()


def test_prompt_hash_routes_on_prompt_set_id(two_set_registry):
    ha = P.get_prompt_hash("history", "flashcards", "uz", prompt_set_id="set-a")
    hb = P.get_prompt_hash("history", "flashcards", "uz", prompt_set_id="set-b")
    assert ha != hb


def test_default_prompt_set_id_follows_the_manifest_registration_not_hardcode(two_set_registry):
    # Sanity: with no manifest default of "homework-v1" registered at all in this
    # tmp registry, an unqualified call must still resolve via the module-level
    # LEGACY_PROMPT_SET_ID default kwarg (not the manifest's "default" key) --
    # PR A pins the kwarg default, it does not consult manifest["default"].
    with pytest.raises(KeyError, match="unknown prompt set"):
        P.get_prompt("history", "flashcards")


# --- Judge and solver thread prompt_set_id into their built-in-contract fallback --

_JUDGE_KW = dict(
    subject="history", phase_name="flashcards", output_md="OUT",
    lesson_context="SRC", prior_outputs={},
    gen_provider="claude", gen_model="claude-sonnet-4-6",
    judge_provider="claude", judge_model="claude-sonnet-4-6",
)

_SOLVER_KW = dict(
    subject="history", phase_name="flashcards", phase_output_md="OUT",
    lesson_context="SRC", prior_outputs={},
    solver_provider="claude", solver_model="claude-sonnet-4-6",
)


def _capturing_run_phase(captured):
    async def _fake(**kwargs):
        captured["phase_prompt"] = kwargs["phase_prompt"]
        return types.SimpleNamespace(parsed=Verdict(passed=True))
    return _fake


def _capturing_solve_run_phase(captured):
    async def _fake(**kwargs):
        captured["phase_prompt"] = kwargs["phase_prompt"]
        return types.SimpleNamespace(parsed=SolveVerdict(discrepancies=[]))
    return _fake


@pytest.mark.asyncio
async def test_judge_routes_built_in_contract_by_prompt_set_id(two_set_registry, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    await pj.judge(contract_override=None, prompt_set_id="set-b", **_JUDGE_KW)
    assert _SET_B_SENTINEL in captured["phase_prompt"]
    assert _SET_A_SENTINEL not in captured["phase_prompt"]


@pytest.mark.asyncio
async def test_judge_default_prompt_set_id_is_legacy(monkeypatch):
    # No tmp registry here -- this exercises the REAL homework-v1 tree, proving
    # the keyword-only default (`LEGACY_PROMPT_SET_ID`) is what an unqualified
    # judge() call gets, matching every pre-existing call site.
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    await pj.judge(contract_override=None, **_JUDGE_KW)
    expected = P.get_prompt("history", "flashcards", output_language="uz")
    assert expected in captured["phase_prompt"]


@pytest.mark.asyncio
async def test_judge_contract_override_bypasses_prompt_set_routing(two_set_registry, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_run_phase(captured))
    await pj.judge(contract_override="CUSTOM-CONTRACT-SENTINEL", prompt_set_id="set-b",
                    **_JUDGE_KW)
    assert "CUSTOM-CONTRACT-SENTINEL" in captured["phase_prompt"]
    assert _SET_B_SENTINEL not in captured["phase_prompt"]


@pytest.mark.asyncio
async def test_solver_routes_built_in_contract_by_prompt_set_id(two_set_registry, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_solve_run_phase(captured))
    await solver.solve(contract_override=None, prompt_set_id="set-b", **_SOLVER_KW)
    assert _SET_B_SENTINEL in captured["phase_prompt"]
    assert _SET_A_SENTINEL not in captured["phase_prompt"]


@pytest.mark.asyncio
async def test_solver_default_prompt_set_id_is_legacy(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent, "run_phase", _capturing_solve_run_phase(captured))
    await solver.solve(contract_override=None, **_SOLVER_KW)
    expected = P.get_prompt("history", "flashcards", output_language="uz")
    assert expected in captured["phase_prompt"]
