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


# --- Task 5: strict predicate (language/humanities family gate) --------------

def test_strict_drops_english_prose_glosses():
    book = "Neutral source text with no relation to the summary examples at all."
    summary = (
        "Ular sinonimlarni bilishadi: (likes/dislikes), (cycling/bikes), "
        "(*was/were*), tanlang shall/should)."
    )
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert not any("likes/dislikes" in c for c in cands)
    assert not any("cycling/bikes" in c for c in cands)
    assert not any("was/were" in c for c in cands)
    assert not any("shall/should" in c for c in cands)


def test_strict_drops_history_prose_glosses():
    book = "Neutral source text unrelated to the drifted summary content below."
    summary = "Bu yerda (tale/narration) va (kompyuter/hisoblagich) haqida gap bor."
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert not any("tale/narration" in c for c in cands)
    assert not any("kompyuter/hisoblagich" in c for c in cands)


def test_strict_keeps_digit_bearing_real_hits():
    book = "Neutral source text unrelated to the drifted fractions below."
    summary = "Natijalar: 1/3, 9/10, 3/4 va (2h/g) koʻrinishida."
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert any("1/3" in c for c in cands)
    assert any("9/10" in c for c in cands)
    assert any("3/4" in c for c in cands)
    assert any("2h/g" in c for c in cands)


def test_strict_keeps_digitless_equals_formula():
    # No digit, but has '=' — humanities subjects (iqtisodiyot/huquq/chqbt)
    # have zero corpus data and could carry digitless '=' formulas.
    book = "Neutral source text unrelated to the drifted formula below."
    summary = "Formula: (yaim=c+i+g) tarzida ifodalanadi."
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert any("yaim=c+i+g" in c for c in cands)


def test_strict_keeps_superscript_unit_because_isdigit_true():
    # '²'.isdigit() is True ('.isdecimal()' is False) — deliberate, matches
    # extract_math_expressions's own use of isdigit. Real geografiya data
    # depends on this: kishi/km²) survives strict only because of it.
    assert "²".isdigit() is True
    assert "²".isdecimal() is False
    book = "Neutral source text unrelated to the drifted density figure below."
    summary = "Zichlik: (120 kishi/km²) koʻrsatkichida."
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert any("kishi/km" in c and "²" in c for c in cands)


def test_strict_default_is_unchanged_digitless_algebra_still_captured():
    # strict=False (default) must remain byte-identical: the digitless algebra
    # arm that strict=True narrows must still work when strict is not passed.
    book = "Kasrlarni qoʻshamiz. Namuna: a/b + c/d."
    summary = "Ishlangan misol: (a−b)/(a+b) + (a−b)²/(a+b)."
    cands = extract_fidelity_candidates(summary, book)
    assert any("(a-b)/(a+b)" in c for c in cands)


def test_strict_applied_before_cap_real_hit_survives_gloss_flood():
    # 15 digitless glosses (more than _FIDELITY_MAX_CANDIDATES=12), each
    # normalized to a "(...)" token that sorts BEFORE the digit-leading real
    # hit '9/10' (ASCII '(' < '9' < 'a'). If strict were applied AFTER the
    # slice, the cap would keep only the first 12 (all glosses) and '9/10'
    # would never make the list — pinning that strict must run BEFORE the cap.
    letters = "abcdefghijklmno"  # 15 distinct, digit-free suffixes
    glosses = [f"(al{ch}word/be{ch}word)" for ch in letters]
    summary = " ".join(glosses) + " Natija 9/10 boʻladi."
    book = "Neutral source text unrelated to any of the drifted content above."
    cands = extract_fidelity_candidates(summary, book, strict=True)
    assert any("9/10" in c for c in cands)
    assert len(cands) <= 12
