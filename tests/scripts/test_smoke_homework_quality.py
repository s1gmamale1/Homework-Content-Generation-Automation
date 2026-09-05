"""Offline contract tests for the bounded homework-quality model harness."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import smoke_homework_quality as smoke


def _fixture(fixture_id: str):
    return next(f for f in smoke.load_fixtures() if f.fixture_id == fixture_id)


def _judge_outcome(*, available=True, has_major=False, warnings=(), feedback=""):
    return SimpleNamespace(
        available=available,
        passed=available and not has_major,
        has_major=has_major,
        warnings=list(warnings),
        feedback=feedback,
        refused=False,
    )


def _solver_outcome(*, available=True, has_mismatch=False, warnings=(), feedback=""):
    return SimpleNamespace(
        available=available,
        agrees=available and not has_mismatch,
        has_mismatch=has_mismatch,
        warnings=list(warnings),
        feedback=feedback,
        refused=False,
    )


def test_catalog_maps_all_original_findings_and_cross_subject_controls():
    from app.services.subjects import REGISTRY

    fixtures = smoke.load_fixtures()
    defect_ids = {defect for f in fixtures for defect in f.defect_ids}
    tags = {tag for f in fixtures for tag in f.control_tags}

    assert {f"F{i:02d}" for i in range(1, 15)} <= defect_ids
    assert {
        "constructed-math-data",
        "supplied-science-observations",
        "open-l2-passage",
        "attributed-history-excerpt",
        "math-equivalence",
        "science-category-overlap",
        "l2-synonym",
        "expanded-recall-name",
    } <= tags
    assert {f.subject for f in fixtures} >= {"history", "matematika", "biology", "english"}
    assert {f.subject for f in fixtures} <= set(REGISTRY)


def test_every_pair_has_one_negative_and_one_positive_with_shared_scope():
    fixtures = smoke.load_fixtures()
    by_pair = smoke.group_pairs(fixtures)

    assert len(by_pair) >= 14
    for pair in by_pair.values():
        assert {f.variant for f in pair} == {"negative", "positive"}
        assert len(pair) == 2
        assert len({f.reviewer for f in pair}) == 1
        assert len({f.defect_ids for f in pair}) == 1


def test_original_rabot_negative_is_the_unchanged_full_artifact():
    original = _fixture("F03-history-rabot-negative")

    assert original.output_sha256 == "d360cdd6f5abe636331a843a7be4f6606b8815c9f57da5592f258e66be63ad46"
    assert "B) Rabot" in original.output_md
    assert "Noto'g'ri (B): Karvonlar to'xtaydigan barcha bekatlarni harbiy istehkom" in original.output_md
    assert "### fill_blank — card 3" in original.output_md
    assert "**Muqobil javoblar:** Pomir tog'i" in original.output_md


def test_catalog_validation_rejects_a_missing_counterpart():
    fixtures = smoke.load_fixtures()
    broken = [f for f in fixtures if f.fixture_id != "F01-source-positive"]

    with pytest.raises(smoke.FixtureError, match="negative and positive"):
        smoke.validate_fixtures(broken)


def test_negative_judge_needs_expected_severity_and_decisive_evidence():
    fixture = _fixture("F01-source-negative")

    unrelated = smoke.classify_result(
        fixture,
        _judge_outcome(has_major=True, warnings=["[major] wrong number of headings"]),
    )
    intended = smoke.classify_result(
        fixture,
        _judge_outcome(
            has_major=True,
            warnings=["[major] `Who wrote the Sian chronicle?` has no supplied author or excerpt"],
        ),
    )

    assert unrelated.status == "unmet"
    assert unrelated.decisive_evidence == ()
    assert intended.status == "met"
    assert "Sian chronicle" in intended.decisive_evidence[0]


def test_clarity_case_does_not_require_a_major_but_still_needs_matching_evidence():
    fixture = _fixture("F13-map-clarity-negative")

    result = smoke.classify_result(
        fixture,
        _judge_outcome(
            has_major=False,
            warnings=["[minor] `Tarixiy xaritalarni tahlil qilmay` refers to a map that is not shown"],
        ),
    )

    assert fixture.expected_outcome == "finding"
    assert result.status == "met"


def test_unavailable_or_refused_is_unverified_for_both_variants():
    for fixture_id, outcome in (
        ("F01-source-negative", _judge_outcome(available=False)),
        ("F01-source-positive", _judge_outcome(available=False)),
        ("F03-history-rabot-negative", _solver_outcome(available=False)),
    ):
        result = smoke.classify_result(_fixture(fixture_id), outcome)
        assert result.status == "unverified"


def test_solver_requires_high_mismatch_and_the_planted_answer_evidence():
    fixture = _fixture("F03-history-rabot-negative")

    low_only = smoke.classify_result(
        fixture,
        _solver_outcome(warnings=["[medium] B) Rabot may be defensible"]),
    )
    wrong_high = smoke.classify_result(
        fixture,
        _solver_outcome(has_mismatch=True, warnings=["[high] card count is wrong"]),
    )
    intended = smoke.classify_result(
        fixture,
        _solver_outcome(
            has_mismatch=True,
            warnings=["[high] B) Rabot is also defensible because rabot can mean caravanserai"],
        ),
    )

    assert low_only.status == "unmet"
    assert wrong_high.status == "unmet"
    assert intended.status == "met"


def test_positive_accepts_minor_advice_but_not_major_or_high_mismatch():
    judge_fixture = _fixture("F10-style-positive")
    solver_fixture = _fixture("C01-math-equivalence-positive")

    assert smoke.classify_result(
        judge_fixture, _judge_outcome(warnings=["[minor] concise wording could improve"])
    ).status == "met"
    assert smoke.classify_result(
        judge_fixture, _judge_outcome(has_major=True, warnings=["[major] unanswerable"])
    ).status == "unmet"
    assert smoke.classify_result(
        solver_fixture, _solver_outcome(warnings=["[medium] wording advice"])
    ).status == "met"
    assert smoke.classify_result(
        solver_fixture, _solver_outcome(has_mismatch=True, warnings=["[high] wrong key"])
    ).status == "unmet"


def test_pair_aggregation_fails_for_unmet_or_unverified_member():
    results = [
        {"pair_id": "p", "variant": "negative", "status": "met"},
        {"pair_id": "p", "variant": "positive", "status": "unverified"},
        {"pair_id": "q", "variant": "negative", "status": "met"},
        {"pair_id": "q", "variant": "positive", "status": "met"},
    ]

    pairs = {row["pair_id"]: row for row in smoke.aggregate_pairs(results)}
    assert pairs["p"]["status"] == "unverified"
    assert pairs["q"]["status"] == "met"
    assert smoke.result_exit_code(results) == 1
    assert smoke.result_exit_code(results[2:]) == 0


def test_result_sanitization_removes_credentials_and_token_urls():
    payload = {
        "fixture_id": "safe",
        "authorization": "Bearer secret-value",
        "api_key": "secret-key",
        "warnings": ["fetch https://example.test/a?token=abc&x=1", "Bearer xyz"],
        "usage": {"output_tokens": 41},
    }

    clean = smoke.sanitize_payload(payload)
    rendered = smoke.json_dumps(clean)

    assert clean["fixture_id"] == "safe"
    assert clean["usage"]["output_tokens"] == 41
    assert "secret-value" not in rendered
    assert "secret-key" not in rendered
    assert "token=abc" not in rendered
    assert "Bearer xyz" not in rendered


def test_reported_model_is_never_filled_from_the_requested_model():
    assert smoke.reported_model({}) is None
    assert smoke.reported_model({"modelVersion": "gemini-actual"}) == "gemini-actual"
    assert smoke.reported_model({"model": "claude-actual"}) == "claude-actual"


@pytest.mark.asyncio
async def test_runner_calls_production_judge_boundary_with_standalone_probe_fields():
    fixture = _fixture("F01-source-negative")
    calls = []

    async def fake_judge(**kwargs):
        calls.append(kwargs)
        return _judge_outcome(
            has_major=True,
            warnings=["[major] Who wrote the Sian chronicle? No author is supplied."],
        )

    config = smoke.RunConfig(provider="gemini", model="gemini-explicit", transport="api")
    result = await smoke.run_fixture(fixture, config, judge_call=fake_judge)

    assert result["status"] == "met"
    assert result["subject"] == "history"
    assert result["phase_name"] == "case-based-preview"
    assert result["output_language"] == "uz"
    assert result["control_tags"] == ["attributed-history-excerpt"]
    assert result["source_refs"] == [
        "https://www.perseus.tufts.edu/hopper/text?doc=Hdt.%205.52"
    ]
    assert calls[0]["contract_override"] == fixture.contract
    assert calls[0]["homework_job_id"] is None
    assert calls[0]["phase_output_id"] is None
    assert calls[0]["transport"] == "api"
    assert result["model"] == {
        "requested_provider": "gemini",
        "requested_model": "gemini-explicit",
        "effective_provider": "gemini",
        "effective_model": "gemini-explicit",
        "reported_model": None,
    }


def test_default_cli_is_list_only_and_real_run_requires_explicit_bounded_scope(capsys):
    assert smoke.main([]) == 0
    assert "F01-source" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        smoke.main(["--run"])
    with pytest.raises(SystemExit):
        smoke.main([
            "--run", "--provider", "gemini", "--model", "m", "--transport", "api",
            "--pair", "F01-source", "--pair", "F02-proof", "--pair", "F03-history-rabot",
            "--pair", "F04-branch", "--pair", "F05-geography",
        ])
    with pytest.raises(SystemExit):
        smoke.main([
            "--run", "--provider", "gemini", "--model", "m", "--transport", "cli",
            "--pair", "F01-source",
        ])


def test_real_run_rejects_a_model_outside_the_production_manifest(monkeypatch, tmp_path):
    async def dangerous_execute(*_args, **_kwargs):
        raise AssertionError("invalid model must be rejected before any reviewer call")

    monkeypatch.setattr(smoke, "execute_run", dangerous_execute)
    with pytest.raises(SystemExit):
        smoke.main([
            "--run", "--provider", "gemini", "--model", "made-up-model",
            "--transport", "api", "--pair", "F01-source",
            "--output", str(tmp_path / "result.json"),
        ])


def test_real_run_refuses_to_overwrite_a_credential_named_file(monkeypatch, tmp_path):
    async def dangerous_execute(*_args, **_kwargs):
        raise AssertionError("credential-like output must be rejected before reviewer calls")

    monkeypatch.setattr(smoke, "execute_run", dangerous_execute)
    with pytest.raises(SystemExit):
        smoke.main([
            "--run", "--provider", "gemini", "--model", "gemini-3.7-flash",
            "--transport", "api", "--pair", "F01-source",
            "--output", str(tmp_path / ".qa-credentials.json"),
        ])


def test_write_report_persists_only_sanitized_json(tmp_path: Path):
    target = tmp_path / "result.json"
    smoke.write_report(target, {"authorization": "Bearer nope", "status": "unverified"})

    text = target.read_text(encoding="utf-8")
    assert "Bearer nope" not in text
    assert '"status": "unverified"' in text
