import types

import pytest

from app.services import golden_eval as ge


@pytest.mark.asyncio
async def test_deterministic_score_packet_and_diff_detects_regression():
    entry = ge.load_golden_set()[0]
    clean = [ge.PhaseView("flashcards", "Toza matn.", "ok", None, None),
             ge.PhaseView("reflection", "Agar ilova belgilasa...", "ok", None, None)]
    dirty = [ge.PhaseView("flashcards", "Mode: Hard atamа", "ok", None, None),
             ge.PhaseView("reflection", "Needs Retry", "ok", None, None)]
    base = await ge.score_packet(entry, clean, "src", "next", provider="gemini",
                                 model="gemini-2.5-pro", transport="api", llm=False)
    cur = await ge.score_packet(entry, dirty, "src", "next", provider="gemini",
                                model="gemini-2.5-pro", transport="api", llm=False)
    assert base.scores["language"].verdict == "pass"
    assert cur.scores["language"].verdict == "flag"
    regressions = ge.diff_scores(base, cur)
    assert any("language" in r for r in regressions)
    assert ge.diff_scores(base, base) == []   # identical → no regression


@pytest.mark.asyncio
async def test_deterministic_packet_score_omits_llm_only_dims():
    entry = ge.load_golden_set()[0]
    phases = [ge.PhaseView("flashcards", "Toza matn.", "ok", None, None)]
    result = await ge.score_packet(
        entry, phases, "src", "next", provider="gemini", model="gemini-2.5-pro",
        transport="api", llm=False,
    )
    assert "boundary" not in result.scores
    assert "answer_key" not in result.scores
    assert "extract_fidelity" not in result.scores
    # broken_question stays present (deterministic half always runs) but is
    # marked deterministic-only when llm=False.
    assert result.scores["broken_question"].mechanism == "deterministic"
    assert result.job_id == entry.job_id


@pytest.mark.asyncio
async def test_broken_question_e1_merge_flags_when_either_half_flags(monkeypatch):
    entry = ge.load_golden_set()[0]
    # deterministic half: a clean error-detection phase (single broken block).
    phases = [
        ge.PhaseView(
            "practice-error-detection",
            "Blok 3 noto'g'ri.\n## Reveal\nXato blok 3.",
            "ok", None, None,
        ),
    ]

    async def fake_run_phase(**kw):
        return types.SimpleNamespace(
            parsed=types.SimpleNamespace(verdict="flag", severity="major", evidence="llm caught it"),
            text="", usage={"prompt_tokens": 10, "output_tokens": 5, "cached_tokens": 0},
        )

    monkeypatch.setattr(ge.agent, "run_phase", fake_run_phase)
    result = await ge.score_packet(
        entry, phases, "src", "next", provider="gemini", model="gemini-2.5-pro",
        transport="api", llm=True,
    )
    bq = result.scores["broken_question"]
    assert bq.verdict == "flag"  # LLM half flagged even though deterministic half passed
    assert bq.mechanism == "llm"
    assert "llm" in bq.detail.lower()


@pytest.mark.asyncio
async def test_diff_scores_flips_exit_intent_on_seeded_pass_to_flag():
    entry = ge.load_golden_set()[0]
    clean = [ge.PhaseView("reflection", "Agar natija yomon bo'lsa, qayta ishlang.", "ok", None, None)]
    dirty = [ge.PhaseView("reflection", "Natija kuzatildi. Needs Retry.", "ok", None, None)]
    base = await ge.score_packet(entry, clean, "src", "next", provider="gemini",
                                 model="gemini-2.5-pro", transport="api", llm=False)
    cur = await ge.score_packet(entry, dirty, "src", "next", provider="gemini",
                                model="gemini-2.5-pro", transport="api", llm=False)
    assert base.scores["reflection"].verdict == "pass"
    assert cur.scores["reflection"].verdict == "flag"
    regressions = ge.diff_scores(base, cur)
    # This is exactly the signal scripts/golden_eval.py's --baseline gate uses
    # to decide its exit code: non-empty regressions => exit 1.
    assert len(regressions) > 0
    assert any("reflection" in r for r in regressions)


@pytest.mark.asyncio
async def test_packet_score_json_round_trip_preserves_diff_semantics():
    entry = ge.load_golden_set()[0]
    clean = [ge.PhaseView("reflection", "Agar natija yomon bo'lsa, qayta ishlang.", "ok", None, None)]
    base = await ge.score_packet(entry, clean, "src", "next", provider="gemini",
                                 model="gemini-2.5-pro", transport="api", llm=False)
    roundtripped = ge.packet_score_from_dict(ge.packet_score_to_dict(base))
    assert roundtripped.job_id == base.job_id
    assert roundtripped.scores["reflection"].verdict == base.scores["reflection"].verdict
    assert ge.diff_scores(roundtripped, base) == []
