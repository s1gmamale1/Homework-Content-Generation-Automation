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
