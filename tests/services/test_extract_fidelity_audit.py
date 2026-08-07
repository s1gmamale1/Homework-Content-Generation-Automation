"""Unit tests for the extract-fidelity audit's data models + pure helpers.

Pure only — no DB, no PDF, no network (repo bar: pass WITHOUT
RUN_DB_INTEGRATION=1). `load_extract_audit_inputs` itself is exercised by a
later task; it needs a real DB + PDF to test meaningfully. Task 3's
`audit_one`/`audit_with_control` DO make an LLM call in production, but here
`agent.run_phase` is always patched with `unittest.mock.AsyncMock` — zero
real model calls, zero DB, zero PDF reads in this file. The CLI
crash-safety tests near the end patch `scripts.extract_fidelity_audit`'s DB
fetch / PDF read / `run_phase` the same way — still zero DB/PDF/model calls.
"""
import dataclasses
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.services import extract_fidelity_audit as efa
from app.services.agent import PhaseResult
from scripts import extract_fidelity_audit as cli


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


def _clause_paragraph(prompt: str, heading_marker: str) -> str:
    """Locate the (lowercased) paragraph whose text contains
    `heading_marker`, splitting on blank lines ("\n\n"). Fails loudly if
    zero or more-than-one paragraph matches, so a deleted clause fails
    THIS lookup rather than silently falling through to some other
    paragraph that happens to share vocabulary (e.g. "translation" also
    appears in the status-definitions bullet list, not just in the
    dedicated Translation-tolerance clause paragraph)."""
    paragraphs = prompt.split("\n\n")
    matches = [p for p in paragraphs if heading_marker in p.lower()]
    assert len(matches) == 1, (
        f"expected exactly one paragraph containing {heading_marker!r}, "
        f"found {len(matches)}"
    )
    return matches[0].lower()


def test_prompt_has_translation_tolerance_clause():
    # Locate the dedicated clause paragraph by its own heading (not just
    # any "translat" occurrence — that word also appears in the
    # status-definitions bullets) so a deletion of THIS clause fails THIS
    # test, not a coincidental match elsewhere.
    prompt = efa.build_adjudicator_prompt(_inputs())
    para = _clause_paragraph(prompt, "translation-tolerance clause")
    # Must explicitly say translation (etc.) is NOT drift / is ok, WITHIN
    # this clause's own paragraph.
    assert "not drift" in para or "is ok" in para or "is `ok`" in para


def test_prompt_has_omission_is_not_drift_clause():
    prompt = efa.build_adjudicator_prompt(_inputs())
    para = _clause_paragraph(prompt, "omission-is-not-drift clause")
    assert "not drift" in para


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


# ---------- Mutation ----------


def test_mutation_is_frozen_with_expected_fields():
    import dataclasses

    assert dataclasses.is_dataclass(efa.Mutation)
    field_names = {f.name for f in dataclasses.fields(efa.Mutation)}
    assert field_names == {"kind", "original", "replacement", "offset"}
    mutation = efa.Mutation(kind="date", original="1799", replacement="1815", offset=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutation.kind = "name"


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


# Production-shaped fixtures: a `## heading` plus `- Term: definition`
# bullets, the dominant real definitional markup (round-1 fix). The
# original em/en-dash-only regex yields ZERO candidates on these — that
# gap (prose fixtures passing while realistic bulleted extract markdown
# silently produced nothing) is what the round-1 review caught.
_DEF_BULLET_MD_UZ = (
    "## Bargli o'simliklar\n"
    "- Tashqi belgilari: keng bargli, yashil rangli, tuksiz shakl.\n"
    "- Ichki tuzilishi: mayda hujayralardan tashkil topgan to'qima.\n"
)
_DEF_BULLET_MD_RU = (
    "## Заголовок раздела\n"
    "- Термин: короткое определение данного понятия.\n"
    "- Другой термин: совершенно иное определение здесь.\n"
)
_DEF_BOLD_MD = (
    "## Voqealar\n"
    "**486-yil**: Bu davrda muhim voqealar yuz berdi.\n"
    "**1204-yil**: Boshqa muhim voqea shu yili sodir bo'ldi.\n"
)


def test_definition_candidates_finds_bullet_colon_form_uzbek():
    candidates = efa._definition_candidates(_DEF_BULLET_MD_UZ)
    assert len(candidates) >= 2
    assert len({c[0] for c in candidates}) >= 2  # distinct predicate texts


def test_definition_candidates_finds_bullet_colon_form_russian():
    candidates = efa._definition_candidates(_DEF_BULLET_MD_RU)
    assert len(candidates) >= 2
    assert len({c[0] for c in candidates}) >= 2


def test_definition_candidates_finds_bold_colon_form():
    candidates = efa._definition_candidates(_DEF_BOLD_MD)
    assert len(candidates) >= 2
    assert len({c[0] for c in candidates}) >= 2


def test_inject_mutation_definition_finds_uzbek_bullet_colon_candidates():
    result = efa.inject_mutation(_DEF_BULLET_MD_UZ, "definition", seed=1, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _DEF_BULLET_MD_UZ
    assert mutation.kind == "definition"
    assert mutation.original != mutation.replacement


def test_inject_mutation_definition_finds_russian_bullet_colon_candidates():
    result = efa.inject_mutation(_DEF_BULLET_MD_RU, "definition", seed=1, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _DEF_BULLET_MD_RU
    assert mutation.kind == "definition"
    assert mutation.original != mutation.replacement


def test_inject_mutation_definition_finds_bold_colon_candidates():
    result = efa.inject_mutation(_DEF_BOLD_MD, "definition", seed=1, forbidden_text="")
    assert result is not None
    mutated, mutation = result
    assert mutated != _DEF_BOLD_MD
    assert mutation.kind == "definition"
    assert mutation.original != mutation.replacement


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


def test_reground_and_gate_rejects_high_char_low_token_span():
    # "internationalization" is 21 chars (>= _REGROUND_MIN_CHARS) but a
    # SINGLE token (< _REGROUND_MIN_TOKENS). Under a correct AND-gate this
    # must NOT downgrade; a buggy OR-gate would downgrade it purely on the
    # char-length condition. Must actually be present in whole_book_text so
    # the char-length arm alone is genuinely satisfied.
    whole = "The reconstruction required extensive internationalization efforts."
    claims = [_verdict("unsupported", span="internationalization")]
    assert len("internationalization") >= 12  # sanity: char condition alone is true
    new_claims, downgraded = efa.reground_unsupported(claims, whole)
    assert downgraded == 0
    assert new_claims[0].status == "unsupported"


def test_reground_and_gate_rejects_high_token_low_char_span():
    # "was signed" is 10 chars (< _REGROUND_MIN_CHARS) but TWO tokens
    # (>= _REGROUND_MIN_TOKENS). Under a correct AND-gate this must NOT
    # downgrade; a buggy OR-gate would downgrade it purely on the
    # token-count condition. Present verbatim in _WHOLE_BOOK.
    claims = [_verdict("unsupported", span="was signed")]
    assert len("was signed") < 12  # sanity: char condition alone is false
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


# ============================================================================
# Task 3 — audit_one / select_mutation / audit_with_control / summarize_runs
# / select_sample. `agent.run_phase` is ALWAYS patched (AsyncMock) — zero
# real model calls anywhere in this section.
# ============================================================================


def _phase_result(claims, usage=None):
    return PhaseResult(
        text="",
        parsed=efa.Adjudication(claims=claims),
        usage=usage if usage is not None else {"prompt_tokens": 100, "output_tokens": 20},
    )


# ---------- audit_one ----------


async def test_audit_one_returns_report_and_appends_exactly_one_call(monkeypatch):
    claims = [_verdict("ok"), _verdict("contradicts"), _verdict("unsupported", span="zzz")]
    mock = AsyncMock(return_value=_phase_result(claims))
    monkeypatch.setattr(efa.agent, "run_phase", mock)

    inputs = _inputs(source_language="uz", output_language="uz")
    calls: list[dict] = []
    report = await efa.audit_one(
        inputs, provider="gemini", model="gemini-3.5-flash", transport="api", calls=calls
    )

    assert isinstance(report, efa.ExtractFidelityReport)
    assert report.ok_count == 1
    assert report.contradicts_count == 1
    assert report.unsupported_count == 1  # "zzz" too short to reground
    assert len(calls) == 1
    assert calls[0]["step"] == "audit"
    assert calls[0]["provider"] == "gemini"
    assert calls[0]["model"] == "gemini-3.5-flash"
    assert calls[0]["usage"] == {"prompt_tokens": 100, "output_tokens": 20}


async def test_audit_one_reports_lesson_metadata_and_cross_language_flag(monkeypatch):
    monkeypatch.setattr(efa.agent, "run_phase", AsyncMock(return_value=_phase_result([])))
    inputs = _inputs(
        job_id="job-123", subject="history", family="humanities", grade="9",
        source_language="ru", output_language="uz",
    )
    report = await efa.audit_one(
        inputs, provider="gemini", model="gemini-3.5-flash", transport="api", calls=[]
    )
    assert report.job_id == "job-123"
    assert report.subject == "history"
    assert report.family == "humanities"
    assert report.grade == "9"
    assert report.source_language == "ru"
    assert report.output_language == "uz"
    assert report.cross_language is True


async def test_audit_one_same_language_is_not_cross_language(monkeypatch):
    monkeypatch.setattr(efa.agent, "run_phase", AsyncMock(return_value=_phase_result([])))
    inputs = _inputs(source_language="uz", output_language="uz")
    report = await efa.audit_one(
        inputs, provider="gemini", model=None, transport="api", calls=[]
    )
    assert report.cross_language is False


async def test_audit_one_passes_xfid_operation_and_no_production_attribution(monkeypatch):
    mock = AsyncMock(return_value=_phase_result([]))
    monkeypatch.setattr(efa.agent, "run_phase", mock)
    inputs = _inputs()
    await efa.audit_one(
        inputs, provider="gemini", model="gemini-3.5-flash", transport="api", calls=[]
    )
    kwargs = mock.await_args.kwargs
    assert kwargs["operation"] == "xfid:audit"
    assert kwargs["homework_job_id"] is None
    assert kwargs["phase_output_id"] is None
    assert kwargs["schema"] is efa.Adjudication
    assert kwargs["transport"] == "api"


async def test_audit_one_runs_reground_and_reports_downgrade_count(monkeypatch):
    # A long multi-word span that IS present in whole_book_text -> downgraded.
    whole = "The treaty was signed in Versailles after long negotiation."
    claims = [_verdict("unsupported", span="the treaty was signed in Versailles")]
    monkeypatch.setattr(efa.agent, "run_phase", AsyncMock(return_value=_phase_result(claims)))
    inputs = _inputs(whole_book_text=whole)
    report = await efa.audit_one(
        inputs, provider="gemini", model=None, transport="api", calls=[]
    )
    assert report.downgraded_count == 1
    assert report.ok_count == 1
    assert report.unsupported_count == 0


async def test_audit_one_reports_claim_type_breakdown(monkeypatch):
    claims = [
        _verdict("ok", claim_type="name"),
        _verdict("contradicts", claim_type="name"),
        _verdict("unsupported", claim_type="date", span="zzz"),
    ]
    monkeypatch.setattr(efa.agent, "run_phase", AsyncMock(return_value=_phase_result(claims)))
    report = await efa.audit_one(
        _inputs(), provider="gemini", model=None, transport="api", calls=[]
    )
    assert report.claim_type_counts["name"] == {"ok": 1, "contradicts": 1, "unsupported": 0}
    assert report.claim_type_counts["date"] == {"ok": 0, "contradicts": 0, "unsupported": 1}


async def test_audit_one_raises_on_run_phase_exception_never_returns_clean_report(monkeypatch):
    monkeypatch.setattr(
        efa.agent, "run_phase", AsyncMock(side_effect=RuntimeError("subprocess died"))
    )
    calls: list[dict] = []
    with pytest.raises(efa.ExtractFidelityAuditError):
        await efa.audit_one(
            _inputs(), provider="gemini", model=None, transport="api", calls=calls
        )
    assert calls == []  # no call recorded on failure


async def test_audit_one_raises_on_unparsed_result(monkeypatch):
    unparsed = PhaseResult(text="not json", parsed=None, usage={})
    monkeypatch.setattr(efa.agent, "run_phase", AsyncMock(return_value=unparsed))
    calls: list[dict] = []
    with pytest.raises(efa.ExtractFidelityAuditError):
        await efa.audit_one(
            _inputs(), provider="gemini", model=None, transport="api", calls=calls
        )
    assert calls == []


# ---------- select_mutation (pure, no LLM) ----------


def test_select_mutation_falls_back_to_a_kind_that_can_plant():
    # No years/capitalized-mid-sentence words present -> "date" and "name"
    # cannot plant; a definition connector IS present -> "definition" must
    # be the one that succeeds. Proves fallback across kinds, not pinning.
    md = "a metaphor — a figure of speech. a simile – a comparison using like."
    result = efa.select_mutation(md, seed=7, forbidden_text="")
    assert result is not None
    kind, mutated_md, mutation = result
    assert kind == "definition"
    assert mutated_md != md
    assert isinstance(mutation, efa.Mutation)


def test_select_mutation_returns_none_when_no_kind_can_plant():
    md = "short plain text with nothing plantable at all here."
    assert efa.select_mutation(md, seed=1, forbidden_text="") is None


def test_select_mutation_is_deterministic_given_same_seed():
    md = "Napoleon led the army. Later, Napoleon crossed the Alps with Wellington nearby."
    r1 = efa.select_mutation(md, seed=42, forbidden_text="")
    r2 = efa.select_mutation(md, seed=42, forbidden_text="")
    assert r1 == r2


# ---------- lesson_seed (pure) ----------


def test_lesson_seed_is_deterministic():
    assert efa.lesson_seed(1, "job-a") == efa.lesson_seed(1, "job-a")


def test_lesson_seed_varies_by_job_id():
    assert efa.lesson_seed(1, "job-a") != efa.lesson_seed(1, "job-b")


def test_lesson_seed_is_an_int():
    assert isinstance(efa.lesson_seed(5, "job-x"), int)


# ---------- audit_with_control (paired) ----------


_MUTABLE_MD = (
    "Napoleon led the army. Later, Napoleon crossed the Alps with Wellington nearby."
)


def _plant(seed=11):
    """Precompute what select_mutation would plant for _MUTABLE_MD, so
    tests can craft mocked adjudicator claims that reference the actual
    planted replacement text."""
    result = efa.select_mutation(_MUTABLE_MD, seed, forbidden_text="")
    assert result is not None
    return result


async def test_audit_with_control_detects_when_only_mutated_arm_flags_span(monkeypatch):
    kind, mutated_md, mutation = _plant()
    flagged_claim = _verdict("contradicts", span=f"a fact about {mutation.replacement} here")
    mock = AsyncMock(return_value=_phase_result([flagged_claim]))
    monkeypatch.setattr(efa.agent, "run_phase", mock)

    inputs = _inputs(extract_md=_MUTABLE_MD, whole_book_text="")
    pristine = efa.ExtractFidelityReport.from_claims([])  # pristine flags nothing

    calls: list[dict] = []
    paired = await efa.audit_with_control(
        inputs, pristine, seed=11, provider="gemini", model=None, transport="api", calls=calls
    )

    assert paired is not None
    assert paired.detected_planted is True
    assert paired.kind == kind
    assert paired.mutation == mutation
    assert len(calls) == 1  # exactly one NEW call — the mutated arm


async def test_audit_with_control_both_arms_flagging_is_a_false_positive_not_a_detection(
    monkeypatch,
):
    kind, mutated_md, mutation = _plant()
    flagged_claim = _verdict("contradicts", span=f"a fact about {mutation.replacement} here")
    mock = AsyncMock(return_value=_phase_result([flagged_claim]))
    monkeypatch.setattr(efa.agent, "run_phase", mock)

    inputs = _inputs(extract_md=_MUTABLE_MD, whole_book_text="")
    # Pristine ALSO flags the same replacement text (pre-existing bias,
    # unrelated to the mutation) -> must NOT count as detected.
    pristine = efa.ExtractFidelityReport.from_claims(
        [_verdict("contradicts", span=f"already suspicious: {mutation.replacement}")]
    )

    paired = await efa.audit_with_control(
        inputs, pristine, seed=11, provider="gemini", model=None, transport="api", calls=[]
    )
    assert paired is not None
    assert paired.detected_planted is False


async def test_audit_with_control_does_not_rerun_pristine_exactly_one_call(monkeypatch):
    kind, mutated_md, mutation = _plant()
    mock = AsyncMock(return_value=_phase_result([]))
    monkeypatch.setattr(efa.agent, "run_phase", mock)

    inputs = _inputs(extract_md=_MUTABLE_MD, whole_book_text="")
    # Pristine built directly (never via a run_phase call) -> if
    # audit_with_control re-ran the pristine audit, mock.call_count would
    # be 2, not 1.
    pristine = efa.ExtractFidelityReport.from_claims([])

    await efa.audit_with_control(
        inputs, pristine, seed=11, provider="gemini", model=None, transport="api", calls=[]
    )
    assert mock.call_count == 1


async def test_audit_with_control_returns_none_and_makes_no_call_when_unplantable(monkeypatch):
    mock = AsyncMock(return_value=_phase_result([]))
    monkeypatch.setattr(efa.agent, "run_phase", mock)

    unplantable_md = "short plain text with nothing plantable at all here."
    inputs = _inputs(extract_md=unplantable_md, whole_book_text="")
    pristine = efa.ExtractFidelityReport.from_claims([])
    calls: list[dict] = []

    result = await efa.audit_with_control(
        inputs, pristine, seed=1, provider="gemini", model=None, transport="api", calls=calls
    )
    assert result is None
    assert mock.call_count == 0
    assert calls == []


async def test_audit_with_control_raises_on_mutated_arm_call_failure(monkeypatch):
    monkeypatch.setattr(
        efa.agent, "run_phase", AsyncMock(side_effect=RuntimeError("dead"))
    )
    inputs = _inputs(extract_md=_MUTABLE_MD, whole_book_text="")
    pristine = efa.ExtractFidelityReport.from_claims([])
    with pytest.raises(efa.ExtractFidelityAuditError):
        await efa.audit_with_control(
            inputs, pristine, seed=11, provider="gemini", model=None, transport="api", calls=[]
        )


# ---------- summarize_runs ----------


def _report(subject, cross_language, status_counts, claim_type_counts=None, downgraded=0):
    return efa.ExtractFidelityReport(
        job_id="j", subject=subject, family="humanities", grade="9",
        source_language="ru" if cross_language else "uz", output_language="uz",
        cross_language=cross_language,
        ok_count=status_counts.get("ok", 0),
        contradicts_count=status_counts.get("contradicts", 0),
        unsupported_count=status_counts.get("unsupported", 0),
        claim_type_counts=claim_type_counts or {},
        downgraded_count=downgraded,
    )


def test_summarize_runs_aggregates_overall_and_per_subject():
    reports = [
        _report("history", False, {"ok": 2, "unsupported": 1}),
        _report("history", False, {"ok": 1, "contradicts": 1}),
        _report("geografiya", False, {"ok": 3}),
    ]
    summary = efa.summarize_runs(reports)
    overall = summary["overall"]["combined"]
    assert overall["lessons"] == 3
    assert overall["ok_count"] == 6
    assert overall["unsupported_count"] == 1
    assert overall["contradicts_count"] == 1

    history = summary["by_subject"]["history"]["combined"]
    assert history["lessons"] == 2
    assert history["ok_count"] == 3
    geografiya = summary["by_subject"]["geografiya"]["combined"]
    assert geografiya["lessons"] == 1


def test_summarize_runs_splits_by_cross_language():
    reports = [
        _report("english", False, {"ok": 5}),  # same-language
        _report("english", True, {"unsupported": 4}),  # cross-language, inflated
    ]
    summary = efa.summarize_runs(reports)
    eng = summary["by_subject"]["english"]
    assert eng["same_language"]["lessons"] == 1
    assert eng["same_language"]["unsupported_count"] == 0
    assert eng["cross_language"]["lessons"] == 1
    assert eng["cross_language"]["unsupported_count"] == 4
    # combined still has both
    assert eng["combined"]["lessons"] == 2
    assert eng["combined"]["unsupported_count"] == 4


def test_summarize_runs_splits_claim_type_breakdown():
    reports = [
        _report("history", False, {"ok": 1}, claim_type_counts={"name": {"ok": 1, "contradicts": 0, "unsupported": 0}}),
        _report("history", False, {"contradicts": 1}, claim_type_counts={"name": {"ok": 0, "contradicts": 1, "unsupported": 0}}),
    ]
    summary = efa.summarize_runs(reports)
    breakdown = summary["by_subject"]["history"]["combined"]["claim_type_counts"]
    assert breakdown["name"] == {"ok": 1, "contradicts": 1, "unsupported": 0}


def test_summarize_runs_carries_downgraded_count():
    reports = [_report("history", False, {"ok": 1}, downgraded=3)]
    summary = efa.summarize_runs(reports)
    assert summary["overall"]["combined"]["downgraded_count"] == 3


def test_summarize_runs_empty_list_is_zero_not_an_error():
    summary = efa.summarize_runs([])
    assert summary["overall"]["combined"]["lessons"] == 0
    assert summary["by_subject"] == {}


# ---------- select_sample (pure) ----------


def _candidate(job_id, grade="9", lang="uz", subject="history", book_id="book-1"):
    return efa.LessonCandidate(
        job_id=job_id, book_id=book_id, subject=subject, grade=grade, source_language=lang
    )


def test_select_sample_returns_all_when_n_is_none():
    cands = [_candidate(f"j{i}") for i in range(5)]
    result = efa.select_sample(cands, None, seed=1)
    assert {c.job_id for c in result} == {c.job_id for c in cands}
    assert len(result) == 5


def test_select_sample_returns_all_when_n_exceeds_population():
    cands = [_candidate(f"j{i}") for i in range(3)]
    result = efa.select_sample(cands, 10, seed=1)
    assert len(result) == 3


def test_select_sample_caps_at_n():
    cands = [_candidate(f"j{i}") for i in range(10)]
    result = efa.select_sample(cands, 4, seed=1)
    assert len(result) == 4
    assert len(set(c.job_id for c in result)) == 4  # no duplicates


def test_select_sample_is_deterministic_given_same_seed():
    cands = [_candidate(f"j{i}", grade=str(i % 3), lang=("uz" if i % 2 else "ru")) for i in range(12)]
    r1 = [c.job_id for c in efa.select_sample(cands, 5, seed=99)]
    r2 = [c.job_id for c in efa.select_sample(cands, 5, seed=99)]
    assert r1 == r2


def test_select_sample_does_not_mutate_input_list():
    cands = [_candidate(f"j{i}") for i in range(5)]
    original_order = list(cands)
    efa.select_sample(cands, 2, seed=1)
    assert cands == original_order


def test_select_sample_stratifies_across_cells_before_repeating_a_cell():
    # Two distinct (grade, language) cells, 3 candidates each. n=2 with
    # stratify=True must draw one from EACH cell, not two from one cell —
    # proves round-robin-over-cells, not a plain random draw.
    cell_a = [_candidate(f"a{i}", grade="5", lang="uz") for i in range(3)]
    cell_b = [_candidate(f"b{i}", grade="9", lang="ru") for i in range(3)]
    result = efa.select_sample(cell_a + cell_b, 2, seed=3, stratify=True)
    cells_hit = {(c.grade, c.source_language) for c in result}
    assert len(result) == 2
    assert cells_hit == {("5", "uz"), ("9", "ru")}


def test_select_sample_single_cell_degrades_to_plain_shuffle():
    cands = [_candidate(f"j{i}", grade="8", lang="uz") for i in range(6)]
    result = efa.select_sample(cands, 3, seed=1, stratify=True)
    assert len(result) == 3
    assert len(set(c.job_id for c in result)) == 3


def test_select_sample_stratify_false_ignores_cells():
    cell_a = [_candidate(f"a{i}", grade="5", lang="uz") for i in range(5)]
    cell_b = [_candidate(f"b{i}", grade="9", lang="ru") for i in range(5)]
    result = efa.select_sample(cell_a + cell_b, 3, seed=1, stratify=False)
    assert len(result) == 3  # just needs to be a valid cap; no cell guarantee


# ============================================================================
# Fix round 1 — CLI crash-safe report persistence
# (scripts/extract_fidelity_audit.py::_run). Everything below patches the
# CLI's own DB fetch, PDF read, and `agent.run_phase` — zero DB, zero PDF,
# zero real model calls.
# ============================================================================

_CLI_MUTABLE_MD = (
    "Napoleon led the army. Later, Napoleon crossed the Alps with Wellington nearby."
)


async def _cli_fake_fetch(subjects):
    return {
        s: [
            efa.LessonCandidate(
                job_id=f"{s}-job-{i}", book_id=f"{s}-book", subject=s,
                grade="8", source_language="en",
            )
            for i in range(6)
        ]
        for s in subjects
    }


async def _cli_fake_load(job_id, *, whole_book_text=None):
    return efa.ExtractAuditInputs(
        job_id=job_id, book_id="b", subject="english", family="languages", grade="8",
        source_language="en", output_language="uz", lesson_title="L",
        page_start=1, page_end=2, extract_md=_CLI_MUTABLE_MD,
        source_text="source", whole_book_text=whole_book_text or "whole",
    )


async def test_cli_run_writes_partial_report_and_reraises_on_mid_run_failure(
    monkeypatch, tmp_path
):
    """A billed-call failure partway through a run must NOT discard results
    already paid for: sample selection is deterministic given
    --sample-seed, so the obvious recovery (re-run the same command) would
    silently re-bill every lesson that already succeeded unless the partial
    results are persisted first. Asserts (a) the exception still surfaces,
    (b) the JSON report file exists, (c) it contains exactly the lessons
    completed before the failure, (d) `completed` is False."""
    call_count = 0

    async def fake_run_phase(**kw):
        nonlocal call_count
        call_count += 1
        if call_count > 2:
            raise RuntimeError("simulated transient API 5xx")
        return PhaseResult(
            text="", parsed=efa.Adjudication(claims=[]),
            usage={"prompt_tokens": 5, "output_tokens": 1},
        )

    monkeypatch.setattr(cli, "_fetch_candidates", _cli_fake_fetch)
    monkeypatch.setattr(efa, "load_extract_audit_inputs", _cli_fake_load)
    monkeypatch.setattr(cli.agent, "read_whole_book_text", lambda path: "whole book text")
    monkeypatch.setattr(cli.agent, "run_phase", fake_run_phase)

    out_path = tmp_path / "report.json"
    args = cli._parse_args([
        "--subject", "english:6", "--limit", "48", "--mutations", "0",
        "--out", str(out_path),
    ])

    with pytest.raises(efa.ExtractFidelityAuditError):
        await cli._run(args)

    # (a) exception surfaced -- proven by pytest.raises above not failing.
    # (b) the report file exists.
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    # (d) completed is False, with the error recorded.
    assert payload["completed"] is False
    assert payload["error"] is not None
    assert "simulated transient API 5xx" in payload["error"]
    # (c) exactly the lessons completed before the failure (2 successful
    # audit_one calls; the 3rd raised before appending to `reports`).
    assert len(payload["completed_job_ids"]) == 2
    assert len(payload["reports"]) == 2
    assert len(payload["calls"]) == 2
    assert set(payload["completed_job_ids"]) == {r["job_id"] for r in payload["reports"]}


async def test_cli_run_partial_report_never_looks_like_a_complete_run(monkeypatch, tmp_path):
    """A partial report must be legible as partial, not silently pass for a
    complete one — completed=False plus a non-None error is the signal a
    downstream reader (or a human) must be able to check without diffing
    call counts."""
    async def dead_run_phase(**kw):
        raise RuntimeError("dead adjudicator")

    monkeypatch.setattr(cli, "_fetch_candidates", _cli_fake_fetch)
    monkeypatch.setattr(efa, "load_extract_audit_inputs", _cli_fake_load)
    monkeypatch.setattr(cli.agent, "read_whole_book_text", lambda path: "whole book text")
    monkeypatch.setattr(cli.agent, "run_phase", dead_run_phase)

    out_path = tmp_path / "report_dead.json"
    args = cli._parse_args([
        "--subject", "english:6", "--limit", "48", "--mutations", "0",
        "--out", str(out_path),
    ])
    with pytest.raises(efa.ExtractFidelityAuditError):
        await cli._run(args)

    payload = json.loads(out_path.read_text())
    assert payload["completed"] is False
    assert payload["error"]
    assert payload["completed_job_ids"] == []
    assert payload["reports"] == []


async def test_cli_run_records_mutation_targets_not_attempted_when_limit_truncates(
    monkeypatch, tmp_path
):
    """When --limit truncates the run mid-way through the mutation arms,
    the JSON must record explicitly which mutation targets were never
    attempted -- so a later calibration read (e.g. Task 4's >=6/8
    sensitivity gate) can't silently under-count the denominator."""
    async def fake_run_phase(**kw):
        return PhaseResult(
            text="", parsed=efa.Adjudication(claims=[]),
            usage={"prompt_tokens": 5, "output_tokens": 1},
        )

    monkeypatch.setattr(cli, "_fetch_candidates", _cli_fake_fetch)
    monkeypatch.setattr(efa, "load_extract_audit_inputs", _cli_fake_load)
    monkeypatch.setattr(cli.agent, "read_whole_book_text", lambda path: "whole book text")
    monkeypatch.setattr(cli.agent, "run_phase", fake_run_phase)

    out_path = tmp_path / "report_truncated.json"
    # 6 candidates, 3 mutation targets -> ceiling 9 calls; --limit 4 forces
    # the run to stop before all 3 mutation arms are attempted.
    args = cli._parse_args([
        "--subject", "english:6", "--limit", "4", "--mutations", "3",
        "--out", str(out_path),
    ])
    rc = await cli._run(args)
    assert rc == 0

    payload = json.loads(out_path.read_text())
    assert payload["completed"] is True
    assert payload["stopped_early"] is True
    calibration = payload["calibration"]
    not_attempted = calibration["mutation_targets_not_attempted"]
    accounted_for = (
        len(not_attempted) + calibration["total_pairs"] + len(calibration["skipped_no_mutation"])
    )
    assert accounted_for == 3  # all 3 mutation targets are accounted for somewhere
    assert len(not_attempted) >= 1  # the cap genuinely left at least one unattempted
