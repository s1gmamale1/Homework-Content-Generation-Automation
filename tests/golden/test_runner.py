"""Regression tests for `scripts/golden_eval.py`'s pure decision helpers.

Covers the CLI runner/gate logic that previously had zero coverage:
  - E4 emit-baseline guard (`_emit_baseline_or_refuse`)
  - `--baseline` regression-diff exit code (`_regression_exit`)
  - `--audit-check` mismatch counting (`_audit_mismatches`)

All synthetic — no DB, no model calls. `PacketScore`/`DimensionScore` are
built by hand; only file I/O is a `tmp_path` JSON round trip.
"""

import json

from app.services import golden_eval as ge
from scripts import golden_eval as runner


def _score(job_id: str, **verdicts: str) -> ge.PacketScore:
    """Builds a synthetic `PacketScore` with one healthy DimensionScore per
    kwarg (dimension=verdict), mechanism defaulting to 'deterministic'."""
    scores = {
        dim: ge.DimensionScore(
            dimension=dim, verdict=verdict, detail=f"{dim} looks {verdict}",
            mechanism="deterministic",
        )
        for dim, verdict in verdicts.items()
    }
    return ge.PacketScore(job_id=job_id, scores=scores)


# --------------------------------------------------------------------------
# 1. E4 emit-baseline guard
# --------------------------------------------------------------------------


def test_emit_baseline_refuses_and_writes_no_file_when_a_dim_is_unavailable(tmp_path):
    score = _score("job-1", boundary="pass", language="pass")
    # Degrade-to-pass on a scorer outage still reads "pass" but the detail
    # names the outage — that's exactly what E4 must catch.
    score.scores["boundary"] = ge.DimensionScore(
        dimension="boundary", verdict="pass",
        detail="scorer-unavailable: Vertex 503, degraded to pass", mechanism="llm",
    )
    out_path = tmp_path / "baseline.json"

    rc = runner._emit_baseline_or_refuse(score, out_path)

    assert rc != 0
    assert not out_path.exists()


def test_emit_baseline_writes_and_returns_zero_when_all_dims_healthy(tmp_path):
    score = _score("job-1", boundary="pass", language="flag")
    out_path = tmp_path / "nested" / "baseline.json"

    rc = runner._emit_baseline_or_refuse(score, out_path)

    assert rc == 0
    assert out_path.exists()
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written == ge.packet_score_to_dict(score)


# --------------------------------------------------------------------------
# 2. --baseline regression-diff exit code
# --------------------------------------------------------------------------


def test_regression_exit_nonzero_on_pass_to_flag_regression():
    baseline = _score("job-1", boundary="pass")
    current = _score("job-1", boundary="flag")

    assert runner._regression_exit(baseline, current) != 0


def test_regression_exit_zero_when_identical():
    baseline = _score("job-1", boundary="pass")
    current = _score("job-1", boundary="pass")

    assert runner._regression_exit(baseline, current) == 0


def test_regression_exit_zero_on_improvement_flag_to_pass():
    baseline = _score("job-1", boundary="flag")
    current = _score("job-1", boundary="pass")

    assert runner._regression_exit(baseline, current) == 0


# --------------------------------------------------------------------------
# 3. --audit-check mismatch counting
# --------------------------------------------------------------------------


def _entry(**audit_verdict: str) -> ge.GoldenEntry:
    return ge.GoldenEntry(
        job_id="job-1", book_id="book-1", subject="math-algebra", grade="8",
        language="uz", source_pages="1-5", audit_verdict=dict(audit_verdict),
    )


def test_audit_mismatches_empty_when_score_matches_manifest():
    entry = _entry(boundary="pass", language="flag")
    score = _score("job-1", boundary="pass", language="flag")

    assert runner._audit_mismatches(entry, score) == []


def test_audit_mismatches_names_the_flipped_dimension():
    entry = _entry(boundary="pass", language="flag")
    score = _score("job-1", boundary="flag", language="flag")

    mismatches = runner._audit_mismatches(entry, score)

    assert mismatches == ["boundary"]


def test_audit_mismatches_skips_omitted_dims_without_counting_them():
    entry = _entry(boundary="pass", answer_key="flag")
    # answer_key omitted entirely (as under --no-llm) — must be SKIPPED, not
    # counted as a mismatch.
    score = _score("job-1", boundary="pass")

    assert runner._audit_mismatches(entry, score) == []
