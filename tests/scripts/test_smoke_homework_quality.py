"""Offline contract tests for the bounded homework-quality model harness."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.phase_judge import JudgeOutcome
from app.services.solver import SolveOutcome
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


def test_negative_evidence_requires_item_and_defect_relationship_groups():
    fixtures = smoke.load_fixtures()
    target = _fixture("F10-style-negative")
    broken = [
        replace(item, decisive_evidence_groups=(("Only collect",),))
        if item.fixture_id == target.fixture_id else item
        for item in fixtures
    ]

    with pytest.raises(smoke.FixtureError, match="item and defect relationship"):
        smoke.validate_fixtures(broken)


def test_f01_positive_uses_verified_rawlinson_excerpt_and_source():
    fixture = _fixture("F01-source-positive")

    assert fixture.source_refs == (
        "https://classics.mit.edu/Herodotus/history.5.v.html",
    )
    assert (
        '"Royal stations exist along its whole length, and excellent caravanserais;"'
        in fixture.output_md
    )
    assert "All along the road there are royal stations" not in fixture.output_md


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


def test_major_detection_requires_intended_evidence_in_that_major_warning():
    fixture = _fixture("F01-source-negative")
    outcome = JudgeOutcome(
        available=True,
        passed=False,
        has_major=True,
        warnings=[
            "[major] format — wrong number of headings",
            "[minor] wording — `Who wrote the Sian chronicle?` has no supplied author",
        ],
        feedback=(
            "Fix all findings: [major] wrong number of headings; [minor] "
            "`Who wrote the Sian chronicle?` has no supplied author"
        ),
    )

    result = smoke.classify_result(fixture, outcome)

    assert result.status == "unmet"
    assert result.decisive_evidence == ()


def test_solver_mismatch_requires_intended_evidence_in_that_high_warning():
    fixture = _fixture("F03-history-rabot-negative")
    outcome = SolveOutcome(
        available=True,
        agrees=False,
        has_mismatch=True,
        warnings=[
            "[high] fill_blank — accepted Pomir tog'i makes the literal sentence redundant",
            "[medium] card 1 — B) Rabot may also be defensible",
        ],
        feedback=(
            "Fix high errors: Pomir tog'i is redundant. Advisory: B) Rabot may also be defensible."
        ),
    )

    result = smoke.classify_result(fixture, outcome)

    assert result.status == "unmet"
    assert result.decisive_evidence == ()


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


def test_same_question_unrelated_finding_cannot_prove_repetition():
    fixture = _fixture("F09-repetition-negative")
    outcome = JudgeOutcome(
        available=True,
        passed=False,
        has_major=True,
        warnings=[
            "[major] visible evidence — Museum application asks for a written chronicle, "
            "but no chronicle is supplied."
        ],
        feedback="Museum application lacks the written chronicle it requests.",
    )

    assert smoke.classify_result(fixture, outcome).status == "unmet"


def test_same_question_answer_leak_cannot_prove_cross_phase_repetition():
    fixture = _fixture("F09-repetition-negative")
    unrelated = _judge_outcome(
        has_major=True,
        warnings=[
            "[major] Museum application repeats the answer 'Sian' in 'choose Sian "
            "as the Silk Road starting city', giving away the choice before the "
            "learner answers."
        ],
    )
    intended = _judge_outcome(
        has_major=True,
        warnings=[
            "[major] Museum application duplicates the earlier preview: it uses "
            "the same task, situation, and reasoning."
        ],
    )

    assert smoke.classify_result(fixture, unrelated).status == "unmet"
    assert smoke.classify_result(fixture, intended).status == "met"


def test_missing_sources_for_grade_cannot_prove_untaught_or_overloaded_rubric():
    fixture = _fixture("F12-rubric-negative")
    unrelated = _judge_outcome(
        has_major=True,
        warnings=[
            "[major] Visible evidence is missing for this Grade 5 task: "
            "'Epigrafik va yakka numizmatik manbalarni taqqoslang' asks to compare "
            "sources, but no epigraphic or numismatic source is supplied."
        ],
    )
    intended = _judge_outcome(
        warnings=[
            "[minor] 'Epigrafik va yakka numizmatik manbalarni taqqoslang' uses "
            "untaught terminology and an untaught source-comparison method."
        ],
    )

    assert smoke.classify_result(fixture, unrelated).status == "unmet"
    assert smoke.classify_result(fixture, intended).status == "met"


def test_same_question_missing_action_finding_cannot_prove_unclear_referents():
    fixture = _fixture("F14-referent-negative")
    outcome = JudgeOutcome(
        available=True,
        passed=False,
        has_major=True,
        warnings=[
            "[major] visible evidence — Passage gives no post-arrival action needed to "
            "answer `What did they do with them there?`."
        ],
        feedback="The action is absent.",
    )

    assert smoke.classify_result(fixture, outcome).status == "unmet"


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


def test_repaired_controls_supply_visible_facts_and_true_ambiguity():
    f09 = _fixture("F09-repetition-negative")
    f11_negative = _fixture("F11-register-negative")
    f11_positive = _fixture("F11-register-positive")
    f12_negative = _fixture("F12-rubric-negative")
    f12_positive = _fixture("F12-rubric-positive")
    f13_negative = _fixture("F13-map-clarity-negative")
    f14_negative = _fixture("F14-referent-negative")
    c05_negative = _fixture("C05-expanded-recall-negative")

    assert "Information card:" in f09.output_md
    assert "began at Sian" in f09.output_md
    assert all("Ma’lumot kartochkasi" in item.output_md for item in (f11_negative, f11_positive))
    assert all("3–2" in item.output_md and "II" in item.output_md for item in (f12_negative, f12_positive))
    assert "3-2" in f13_negative.output_md and "II" in f13_negative.output_md
    assert all(word in f14_negative.output_md for word in ("traders", "porters", "boxes", "moved"))
    assert "so‘zlar banki" in c05_negative.output_md.casefold()
    assert "aynan yozilganidek" in c05_negative.output_md.casefold()


def test_language_metadata_and_contract_describe_the_actual_probe_text():
    expected_english = {
        "F01-source-negative", "F02-proof-negative", "F04-branch-negative",
        "F05-geography-negative", "F06-certainty-negative", "F07-terminology-negative",
        "F08-route-shape-negative", "F09-repetition-negative", "F10-style-negative",
        "F14-referent-negative", "C01-math-equivalence-negative",
        "C02-science-category-negative", "C04-required-data-negative",
    }
    fixtures = {fixture.fixture_id: fixture for fixture in smoke.load_fixtures()}

    for fixture_id in expected_english:
        fixture = fixtures[fixture_id]
        assert fixture.output_language == "en"
        assert "learner-facing microfixture is in English" in fixture.contract
    assert fixtures["C03-l2-synonym-negative"].output_language == "uz"
    assert "Uzbek scaffolding with English target-language terms" in fixtures[
        "C03-l2-synonym-negative"
    ].contract


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
    assert result["output_language"] == "en"
    assert result["control_tags"] == ["attributed-history-excerpt"]
    assert result["source_refs"] == [
        "https://classics.mit.edu/Herodotus/history.5.v.html"
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


@pytest.mark.skipif(
    bool(os.environ.get("GUARDED_TEST_REPO")),
    reason="the session audit hook forbids every subprocess; run this safe invalid-provider check directly",
)
def test_documented_script_entrypoint_reaches_safe_invalid_provider_error(tmp_path):
    output = tmp_path / "must-not-exist.json"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for credential in (
        "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "CLODEX_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT",
    ):
        env.pop(credential, None)
    completed = subprocess.run(
        [
            sys.executable,
            str(smoke.ROOT / "scripts" / "smoke_homework_quality.py"),
            "--run", "--provider", "invalid-review-provider", "--model", "no-model",
            "--transport", "api", "--pair", "F01-source", "--output", str(output),
        ],
        cwd=smoke.ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 2
    assert "production manifest entry" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    assert not output.exists()


def test_write_report_persists_only_sanitized_json(tmp_path: Path):
    target = tmp_path / "result.json"
    smoke.write_report(target, {"authorization": "Bearer nope", "status": "unverified"})

    text = target.read_text(encoding="utf-8")
    assert "Bearer nope" not in text
    assert '"status": "unverified"' in text
