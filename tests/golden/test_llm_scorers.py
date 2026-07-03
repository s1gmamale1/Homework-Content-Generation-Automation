import types

import pytest

from app.services import golden_eval as ge


class _FakeParsed:
    def __init__(self, verdict, severity="major", evidence="e"):
        self.verdict, self.severity, self.evidence = verdict, severity, evidence


def _fake_run_phase_factory(captured, verdict="flag", severity="major", evidence="e"):
    async def fake_run_phase(**kw):
        captured.update(kw)
        return types.SimpleNamespace(parsed=_FakeParsed(verdict, severity, evidence), text="", usage={})

    return fake_run_phase


# --- score_boundary (brief Step 1, verbatim) --------------------------------


async def test_boundary_scorer_passes_source_and_next_lesson_and_maps_verdict(monkeypatch):
    captured = {}

    async def fake_run_phase(**kw):
        captured.update(kw)
        return types.SimpleNamespace(parsed=_FakeParsed("flag"), text="", usage={})

    monkeypatch.setattr(ge.agent, "run_phase", fake_run_phase)
    s = await ge.score_boundary(
        boss_arena_md="... uses the converse ...",
        preview_md="...",
        source_text="Pifagor teoremasi ... (no converse here)",
        next_lesson_title="Pifagor teoremasiga teskari teorema",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "flag" and s.mechanism == "llm"
    assert "teskari" in captured["phase_prompt"]  # next-lesson title threaded in
    assert "Pifagor teoremasi" in captured["phase_prompt"]  # source threaded in
    assert captured["schema"] is ge.RubricVerdict
    assert captured["phase_name"] == "__golden__"
    assert captured["operation"] == "golden:boundary"
    assert captured["transport"] == "api"


async def test_scorer_error_degrades_to_pass_not_crash(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(ge.agent, "run_phase", boom)
    s = await ge.score_boundary(
        boss_arena_md="x",
        preview_md="x",
        source_text="x",
        next_lesson_title="y",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "pass" and "unavailable" in s.detail
    assert s.mechanism == "llm"


# --- the other 3 dimensions: prompt threads inputs + verdict mapping --------


async def test_answer_key_scorer_threads_source_packet_and_solver_status(monkeypatch):
    captured = {}
    monkeypatch.setattr(ge.agent, "run_phase", _fake_run_phase_factory(captured, verdict="pass"))
    s = await ge.score_answer_key(
        packet_md="Javob: x=5",
        source_text="Tenglama yechimi x=5",
        solver_status="corrected",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "pass" and s.mechanism == "llm"
    assert "Tenglama yechimi x=5" in captured["phase_prompt"]
    assert "Javob: x=5" in captured["phase_prompt"]
    assert "corrected" in captured["phase_prompt"]
    assert captured["operation"] == "golden:answer_key"


async def test_broken_question_scorer_threads_packet_and_source_and_maps_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(ge.agent, "run_phase", _fake_run_phase_factory(captured, verdict="flag"))
    s = await ge.score_broken_question(
        packet_md="Savol: ... yechilmaydi ...",
        source_text="source lesson text",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "flag" and s.mechanism == "llm"
    assert "source lesson text" in captured["phase_prompt"]
    assert "yechilmaydi" in captured["phase_prompt"]
    assert captured["operation"] == "golden:broken_question"


async def test_extract_fidelity_scorer_threads_packet_and_source_and_maps_pass(monkeypatch):
    captured = {}
    monkeypatch.setattr(ge.agent, "run_phase", _fake_run_phase_factory(captured, verdict="pass"))
    s = await ge.score_extract_fidelity(
        packet_md="Misol: 2+2=4",
        source_text="Misol: 2+2=4 (darslikda)",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "pass" and s.mechanism == "llm"
    assert "darslikda" in captured["phase_prompt"]
    assert "2+2=4" in captured["phase_prompt"]
    assert captured["operation"] == "golden:extract_fidelity"


async def test_all_four_scorers_degrade_to_pass_on_run_phase_exception(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("api down")

    monkeypatch.setattr(ge.agent, "run_phase", boom)

    s1 = await ge.score_answer_key(
        packet_md="x", source_text="x", provider="gemini", model="gemini-2.5-pro", transport="api"
    )
    s2 = await ge.score_broken_question(
        packet_md="x", source_text="x", provider="gemini", model="gemini-2.5-pro", transport="api"
    )
    s3 = await ge.score_extract_fidelity(
        packet_md="x", source_text="x", provider="gemini", model="gemini-2.5-pro", transport="api"
    )
    for s in (s1, s2, s3):
        assert s.verdict == "pass" and "unavailable" in s.detail and s.mechanism == "llm"


async def test_scorer_degrades_to_pass_when_run_phase_returns_unparsed(monkeypatch):
    async def unparsed(**kw):
        return types.SimpleNamespace(parsed=None, text="", usage={})

    monkeypatch.setattr(ge.agent, "run_phase", unparsed)
    s = await ge.score_boundary(
        boss_arena_md="x",
        preview_md="x",
        source_text="x",
        next_lesson_title="y",
        provider="gemini",
        model="gemini-2.5-pro",
        transport="api",
    )
    assert s.verdict == "pass" and "unavailable" in s.detail
