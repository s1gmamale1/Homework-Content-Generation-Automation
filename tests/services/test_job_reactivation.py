"""Unit tests for ``app.services.job_reactivation.retired_models_in_job``.

Guards the three reactivation paths (job retry, batch resume, relaunch-resume)
against silently re-firing a job that is pinned to a gemini-2.5 model —
retired 2026-08-03, 404s on the production key. A job has four independent
(provider, model) role pairs; any of them may be the retired stamp.
"""
from types import SimpleNamespace

from app.services.job_reactivation import retired_models_in_job


def _job(**overrides):
    base = dict(
        provider=None, model=None,
        extract_provider=None, extract_model=None,
        judge_provider=None, judge_model=None,
        solver_provider=None, solver_model=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_content_role_trips_independently():
    job = _job(provider="gemini", model="gemini-2.5-flash")
    assert retired_models_in_job(job) == [("content", "gemini", "gemini-2.5-flash")]


def test_extract_role_trips_independently():
    job = _job(extract_provider="gemini", extract_model="gemini-2.5-pro")
    assert retired_models_in_job(job) == [("extract", "gemini", "gemini-2.5-pro")]


def test_judge_role_trips_independently():
    job = _job(judge_provider="gemini", judge_model="gemini-2.5-flash-lite")
    assert retired_models_in_job(job) == [("judge", "gemini", "gemini-2.5-flash-lite")]


def test_solver_role_trips_independently():
    job = _job(solver_provider="gemini", solver_model="gemini-2.5-flash")
    assert retired_models_in_job(job) == [("solver", "gemini", "gemini-2.5-flash")]


def test_multiple_roles_trip_independently():
    job = _job(
        provider="gemini", model="gemini-2.5-flash",
        judge_provider="gemini", judge_model="gemini-2.5-pro",
    )
    hits = retired_models_in_job(job)
    assert ("content", "gemini", "gemini-2.5-flash") in hits
    assert ("judge", "gemini", "gemini-2.5-pro") in hits
    assert len(hits) == 2


def test_null_provider_and_model_are_skipped():
    job = _job()
    assert retired_models_in_job(job) == []


def test_null_model_with_provider_set_is_skipped():
    # provider set but model NULL (e.g. a role override left at "provider
    # default") must not be mistaken for a retired stamp.
    job = _job(provider="gemini", model=None)
    assert retired_models_in_job(job) == []


def test_non_gemini_provider_with_same_model_string_does_not_trip():
    # A hypothetical non-gemini provider whose model id happens to collide
    # with a retired gemini model name is NOT retired — the retirement is a
    # gemini-specific fact, not a string match.
    job = _job(provider="claude", model="gemini-2.5-flash")
    assert retired_models_in_job(job) == []


def test_live_model_job_returns_empty():
    job = _job(
        provider="gemini", model="gemini-3.5-flash",
        extract_provider="gemini", extract_model="gemini-3.6-flash",
        judge_provider="claude", judge_model="claude-sonnet-4-6",
        solver_provider="gemini", solver_model="gemini-3.1-flash-lite-preview",
    )
    assert retired_models_in_job(job) == []
