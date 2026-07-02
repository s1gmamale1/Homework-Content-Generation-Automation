# tests/services/test_extract_fidelity.py
from app.services.agent import (
    _normalize_expr,
    extract_math_expressions,
    extract_fidelity_candidates,
)


def test_normalize_unifies_minus_and_slash_and_spaces():
    assert _normalize_expr("−3 / (2a)") == _normalize_expr("-3/(2a)")


def test_extract_math_expressions_captures_operator_forms():
    exprs = extract_math_expressions("Javob: −3/a va tekshiring x=5. Sahifa 12.")
    # fractions/equations captured; the bare page number is not.
    assert any("3/a" in e for e in exprs)
    assert any("x=5" in e.replace(" ", "") for e in exprs)
    assert all("12" != e for e in exprs)


def test_digitless_parenthesized_fraction_is_captured():
    # audited drift #2 (8f734563): invented digitless algebra example. Must be a
    # candidate — no digit, but has '/' AND parentheses.
    exprs = extract_math_expressions("Ayniyat: (a−b)/(a+b) koʻrinishida.")
    assert any(_normalize_expr("(a-b)/(a+b)") == e for e in exprs)


def test_prose_slash_word_is_not_captured():
    # 'va/yoki' has '/' but no digit and no parens → not a candidate (FP guard).
    assert extract_math_expressions("Buni va/yoki oʻqing.") == set()


def test_candidate_is_drifted_expression_absent_from_source():
    book = "Namuna: kasrni qisqartiramiz, natija −3/a. Boshqa misol 21/120."
    summary = "Ishlangan misolda natija −3/(2a) boʻladi; yana 21/100."
    cands = extract_fidelity_candidates(summary, book)
    # both drifted values are ungrounded in the source
    assert any("3/(2a)" in c for c in cands)
    assert any("21/100" in c for c in cands)


def test_digitless_invented_example_is_candidate():
    book = "Kasrlarni qoʻshamiz. Namuna: a/b + c/d."
    summary = "Ishlangan misol: (a−b)/(a+b) + (a−b)²/(a+b)."   # not in source
    cands = extract_fidelity_candidates(summary, book)
    assert any("(a-b)/(a+b)" in c for c in cands)


def test_no_candidates_when_grounded():
    book = "Natija −3/a. Ikkinchi misol 21/120 = 7/40."
    summary = "Misol: −3/a. Yana 21/120."
    assert extract_fidelity_candidates(summary, book) == []


import pytest
from unittest.mock import AsyncMock, patch
from app.services import agent as agent_mod
from app.services.agent import ExtractFidelityVerdict, verify_extract_fidelity


@pytest.mark.asyncio
async def test_verify_returns_mismatches_from_model():
    fake = agent_mod.PhaseResult(
        text="{}",
        parsed=ExtractFidelityVerdict(mismatches=["extract says -3/(2a); source has -3/a"]),
    )
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)) as rp:
        out = await verify_extract_fidelity(
            summary="… -3/(2a) …", book_text="… -3/a …",
            candidates=["-3/(2a)"], provider="gemini", model="gemini-2.5-flash",
            transport="api", homework_job_id=None, phase_output_id=None,
        )
    assert out == ["extract says -3/(2a); source has -3/a"]
    assert rp.call_args.kwargs["schema"] is ExtractFidelityVerdict


@pytest.mark.asyncio
async def test_verify_clean_returns_empty():
    fake = agent_mod.PhaseResult(text="{}", parsed=ExtractFidelityVerdict(mismatches=[]))
    with patch.object(agent_mod, "run_phase", AsyncMock(return_value=fake)):
        out = await verify_extract_fidelity(
            summary="x", book_text="x", candidates=["21/100"],
            provider="gemini", model="gemini-2.5-flash", transport="api",
            homework_job_id=None, phase_output_id=None,
        )
    assert out == []
