"""Unit tests for the extract-fidelity audit's data models + pure helpers.

Pure only — no DB, no PDF, no network (repo bar: pass WITHOUT
RUN_DB_INTEGRATION=1). `load_extract_audit_inputs` itself is exercised by a
later task; it needs a real DB + PDF to test meaningfully.
"""
import pytest
from pydantic import ValidationError

from app.services import extract_fidelity_audit as efa


# ---------- ClaimVerdict ----------


def test_claim_verdict_accepts_valid_status_and_claim_type():
    v = efa.ClaimVerdict(
        claim_span="Napoleon was born in 1769",
        claim_type="date",
        status="ok",
    )
    assert v.status == "ok"
    assert v.claim_type == "date"
    assert v.claim_span == "Napoleon was born in 1769"


@pytest.mark.parametrize("status", ["contradicts", "unsupported", "ok"])
def test_claim_verdict_accepts_every_declared_status(status):
    efa.ClaimVerdict(claim_span="x", claim_type="other", status=status)


def test_claim_verdict_rejects_unknown_status():
    with pytest.raises(ValidationError):
        efa.ClaimVerdict(claim_span="x", claim_type="other", status="maybe")


@pytest.mark.parametrize(
    "claim_type", ["name", "date", "number", "definition", "quote", "term", "other"]
)
def test_claim_verdict_accepts_every_declared_claim_type(claim_type):
    efa.ClaimVerdict(claim_span="x", claim_type=claim_type, status="ok")


def test_claim_verdict_rejects_unknown_claim_type():
    with pytest.raises(ValidationError):
        efa.ClaimVerdict(claim_span="x", claim_type="madeup", status="ok")


def test_claim_verdict_requires_claim_span():
    # GREEN mandates a verbatim claim_span: str field alongside status/claim_type.
    with pytest.raises(ValidationError):
        efa.ClaimVerdict(claim_type="other", status="ok")


# ---------- ExtractFidelityReport aggregation ----------


def _verdict(status, claim_type="other", span="x"):
    return efa.ClaimVerdict(claim_span=span, claim_type=claim_type, status=status)


def test_report_aggregates_per_status_counts():
    claims = [
        _verdict("ok"),
        _verdict("ok"),
        _verdict("contradicts"),
        _verdict("unsupported"),
        _verdict("unsupported"),
        _verdict("unsupported"),
    ]
    report = efa.ExtractFidelityReport.from_claims(claims)
    assert report.ok_count == 2
    assert report.contradicts_count == 1
    assert report.unsupported_count == 3
    assert report.total_count == 6


def test_report_empty_claim_list_is_zero_drift_not_an_error():
    report = efa.ExtractFidelityReport.from_claims([])
    assert report.total_count == 0
    assert report.ok_count == 0
    assert report.contradicts_count == 0
    assert report.unsupported_count == 0


# ---------- _normalize_span ----------


def test_normalize_span_casefolds():
    assert efa._normalize_span("HELLO World") == efa._normalize_span("hello world")


def test_normalize_span_collapses_whitespace_runs():
    assert efa._normalize_span("a   b\n\tc") == "a b c"


def test_normalize_span_strips_leading_trailing_whitespace():
    assert efa._normalize_span("   padded   ") == "padded"


def test_normalize_span_does_not_touch_punctuation_or_math_symbols():
    # Deliberately NOT _normalize_expr-style: no ·*×→*, no −–—→-, no ÷→/,
    # and internal single spaces are preserved (not stripped entirely) —
    # this is prose normalization, not math-expression normalization.
    assert efa._normalize_span("2 × 3 – 1") == "2 × 3 – 1".casefold()


# ---------- ExtractFidelityAuditError ----------


def test_extract_fidelity_audit_error_is_a_runtime_error():
    assert issubclass(efa.ExtractFidelityAuditError, RuntimeError)


# ---------- ExtractAuditInputs is a frozen dataclass with the declared fields ----------


def test_extract_audit_inputs_is_frozen_with_expected_fields():
    import dataclasses

    assert dataclasses.is_dataclass(efa.ExtractAuditInputs)
    field_names = {f.name for f in dataclasses.fields(efa.ExtractAuditInputs)}
    assert field_names == {
        "job_id",
        "book_id",
        "subject",
        "family",
        "grade",
        "source_language",
        "output_language",
        "lesson_title",
        "page_start",
        "page_end",
        "extract_md",
        "source_text",
        "whole_book_text",
    }
    inputs = efa.ExtractAuditInputs(
        job_id="j", book_id="b", subject="s", family="f", grade="5",
        source_language="uz", output_language="uz", lesson_title="L",
        page_start=1, page_end=2, extract_md="e", source_text="s",
        whole_book_text="w",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        inputs.job_id = "other"


def _inputs(**overrides):
    fields = dict(
        job_id="j", book_id="b", subject="s", family="f", grade="5",
        source_language="SRC_LANG_MARKER", output_language="OUT_LANG_MARKER",
        lesson_title="LESSON_TITLE_MARKER",
        page_start=1, page_end=2,
        extract_md="EXTRACT_MD_MARKER",
        source_text="SOURCE_TEXT_MARKER",
        whole_book_text="WHOLE_BOOK_MARKER",
    )
    fields.update(overrides)
    return efa.ExtractAuditInputs(**fields)


# ---------- Adjudication ----------


def test_adjudication_wraps_a_claims_list():
    adj = efa.Adjudication(claims=[_verdict("ok"), _verdict("contradicts")])
    assert len(adj.claims) == 2
    assert all(isinstance(c, efa.ClaimVerdict) for c in adj.claims)


def test_adjudication_accepts_empty_claims_list():
    adj = efa.Adjudication(claims=[])
    assert adj.claims == []


# ---------- build_adjudicator_prompt ----------


def test_prompt_contains_extract_source_and_lesson_title():
    prompt = efa.build_adjudicator_prompt(_inputs())
    assert "EXTRACT_MD_MARKER" in prompt
    assert "SOURCE_TEXT_MARKER" in prompt
    assert "LESSON_TITLE_MARKER" in prompt


def test_prompt_names_the_actual_source_and_output_languages():
    prompt = efa.build_adjudicator_prompt(_inputs())
    assert "SRC_LANG_MARKER" in prompt
    assert "OUT_LANG_MARKER" in prompt


def test_prompt_has_translation_tolerance_clause():
    prompt = efa.build_adjudicator_prompt(_inputs())
    lowered = prompt.lower()
    assert "translat" in lowered
    # Must explicitly say translation (etc.) is NOT drift / is ok.
    assert "not drift" in lowered or "is ok" in lowered or "is `ok`" in lowered


def test_prompt_has_omission_is_not_drift_clause():
    prompt = efa.build_adjudicator_prompt(_inputs())
    lowered = prompt.lower()
    assert "omission" in lowered
    assert "not drift" in lowered


def test_prompt_describes_the_three_statuses():
    prompt = efa.build_adjudicator_prompt(_inputs())
    assert "contradicts" in prompt
    assert "unsupported" in prompt
    assert "\"ok\"" in prompt or "'ok'" in prompt or "`ok`" in prompt


def test_prompt_describes_claims_json_shape():
    prompt = efa.build_adjudicator_prompt(_inputs())
    assert "claim_span" in prompt
    assert "claim_type" in prompt
    assert '"claims"' in prompt or "'claims'" in prompt


# ---------- inject_mutation ----------


_DATE_MD = "In 1799 the war began. It ended in 1815 after a treaty."
_NAME_MD = (
    "Napoleon led the army. Later, Napoleon crossed the Alps with "
    "Wellington nearby."
)
_DEF_MD = (
    "A metaphor — a figure of speech that implies comparison. "
    "A simile – a comparison using like or as."
)


def test_inject_mutation_date_returns_text_that_differs_and_a_mutation_record():
    result = efa.inject_mutation(_DATE_MD, "date", seed=1, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _DATE_MD
    assert isinstance(mutation, efa.Mutation)
    assert mutation.kind == "date"
    assert mutation.original != mutation.replacement
    assert {mutation.original, mutation.replacement} == {"1799", "1815"}
    # offset must be the exact character offset of the replaced span in the
    # ORIGINAL text, and mutated text at that offset must be the replacement.
    assert _DATE_MD[mutation.offset:mutation.offset + len(mutation.original)] == mutation.original
    assert mutated[mutation.offset:mutation.offset + len(mutation.replacement)] == mutation.replacement


def test_inject_mutation_date_with_fewer_than_two_years_returns_none():
    assert efa.inject_mutation("Only one year: 1799 here.", "date", seed=1, forbidden_text="") is None


def test_inject_mutation_date_with_repeated_identical_year_returns_none():
    md = "In 1799 an event happened. Then in 1799 again."
    assert efa.inject_mutation(md, "date", seed=1, forbidden_text="") is None


def test_inject_mutation_is_deterministic_given_same_seed():
    r1 = efa.inject_mutation(_DATE_MD, "date", seed=7, forbidden_text="")
    r2 = efa.inject_mutation(_DATE_MD, "date", seed=7, forbidden_text="")
    assert r1 == r2


def test_inject_mutation_name_excludes_sentence_and_line_initial_words():
    # Both capitalized words are sentence-initial -> zero eligible candidates.
    md = "Napoleon invaded. Wellington defended."
    assert efa.inject_mutation(md, "name", seed=1, forbidden_text="") is None


def test_inject_mutation_name_returns_mutation_from_non_initial_candidates():
    result = efa.inject_mutation(_NAME_MD, "name", seed=3, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _NAME_MD
    assert mutation.kind == "name"
    assert mutation.original != mutation.replacement
    # Both spans must be real substrings that actually occur in the source doc.
    assert mutation.original in _NAME_MD
    assert mutation.replacement in _NAME_MD


def test_inject_mutation_definition_returns_mutation_from_two_predicates():
    result = efa.inject_mutation(_DEF_MD, "definition", seed=2, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _DEF_MD
    assert mutation.kind == "definition"
    assert mutation.original != mutation.replacement


def test_inject_mutation_definition_with_only_one_connector_returns_none():
    md = "A metaphor — a comparison of things."
    assert efa.inject_mutation(md, "definition", seed=1, forbidden_text="") is None


def test_inject_mutation_unknown_kind_raises():
    with pytest.raises(ValueError):
        efa.inject_mutation(_DATE_MD, "bogus", seed=1, forbidden_text="")


def test_inject_mutation_rejects_replacement_present_in_forbidden_text():
    # Only ONE valid direction remains: forbidding "1815" as a replacement
    # value forces original="1815", replacement="1799" (the only candidate
    # NOT present in forbidden_text).
    forbidden = "Some unrelated passage mentions 1815 in passing."
    result = efa.inject_mutation(_DATE_MD, "date", seed=1, forbidden_text=forbidden)
    assert result is not None
    _, mutation = result
    assert mutation.replacement == "1799"
    assert mutation.original == "1815"


def test_inject_mutation_returns_none_when_all_candidates_collide_with_forbidden_text():
    forbidden = "This passage mentions both 1799 and 1815 elsewhere in the book."
    assert efa.inject_mutation(_DATE_MD, "date", seed=1, forbidden_text=forbidden) is None


# ---------- reground_unsupported ----------


_WHOLE_BOOK = (
    "The treaty was signed in Versailles after months of negotiation. "
    "Many years passed, including 1917, before peace fully settled."
)


def test_reground_downgrades_long_multi_word_unsupported_span_found_in_whole_book():
    claims = [_verdict("unsupported", span="the treaty was signed in Versailles")]
    new_claims, downgraded = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert downgraded == 1
    assert new_claims[0].status == "ok"
    assert new_claims[0].claim_span == "the treaty was signed in Versailles"


def test_reground_never_downgrades_contradicts_even_if_span_found():
    claims = [_verdict("contradicts", span="the treaty was signed in Versailles")]
    new_claims, downgraded = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert downgraded == 0
    assert new_claims[0].status == "contradicts"


def test_reground_leaves_unmatched_span_as_unsupported():
    claims = [_verdict("unsupported", span="a completely unrelated invented claim")]
    new_claims, downgraded = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert downgraded == 0
    assert new_claims[0].status == "unsupported"


def test_reground_short_span_is_not_downgraded_even_if_it_matches():
    # "1917" is present verbatim in _WHOLE_BOOK but is far below the minimum
    # token/char threshold -> must NOT be downgraded (a bare token matching
    # a 200KB book is chance, not grounding).
    claims = [_verdict("unsupported", span="1917")]
    new_claims, downgraded = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert downgraded == 0
    assert new_claims[0].status == "unsupported"


def test_reground_does_not_mutate_input_claims_or_return_the_same_list():
    claims = [_verdict("unsupported", span="the treaty was signed in Versailles")]
    new_claims, _ = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert new_claims is not claims
    assert new_claims[0] is not claims[0]
    assert claims[0].status == "unsupported"  # original untouched


def test_reground_returns_downgrade_count_matching_number_of_downgrades():
    claims = [
        _verdict("unsupported", span="the treaty was signed in Versailles"),
        _verdict("unsupported", span="1917"),  # too short, stays unsupported
        _verdict("unsupported", span="a totally invented and unmatched claim"),
        _verdict("ok", span="something already ok"),
    ]
    new_claims, downgraded = efa.reground_unsupported(claims, _WHOLE_BOOK)
    assert downgraded == 1
    assert len(new_claims) == 4
    statuses = [c.status for c in new_claims]
    assert statuses == ["ok", "unsupported", "unsupported", "ok"]
