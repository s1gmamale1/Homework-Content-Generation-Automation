"""Unit tests for the task→model policy (``resolve_provider_chain``).

The policy turns a task type + the job's chosen provider into an ordered
fallback chain for ``run_phase_with_fallback``:

- extract-family tasks are pinned to the cheap extractor in settings,
  regardless of the job provider;
- every other task uses the job provider as primary, then the remaining CLIs
  as fallbacks (no duplicate of the primary; the job model rides only on the
  primary).
"""

from __future__ import annotations

from app.config import settings
from app.services.agent import ProviderSpec, resolve_provider_chain


def test_extract_tasks_pinned_to_cheap_extractor() -> None:
    for task in ("toc.extract", "lesson.extract"):
        chain = resolve_provider_chain(task=task, job_provider="claude", job_model="claude-opus-4-7")
        assert chain == [ProviderSpec(settings.extract_provider, settings.extract_model)]


def test_default_task_uses_job_provider_then_fallbacks() -> None:
    chain = resolve_provider_chain(
        task="case_based_preview",
        job_provider="claude",
        job_model="claude-opus-4-7",
    )
    # Primary is the job's provider+model.
    assert chain[0] == ProviderSpec("claude", "claude-opus-4-7")
    # Fallbacks are the other CLIs, in order, with no model pin.
    fallback_providers = [s.provider for s in chain[1:]]
    assert "claude" not in fallback_providers  # primary not duplicated
    assert fallback_providers == ["gemini", "codex", "kimi"]
    assert all(s.model is None for s in chain[1:])


def test_primary_excluded_from_fallbacks_for_any_provider() -> None:
    chain = resolve_provider_chain(task="flashcards", job_provider="gemini")
    assert chain[0] == ProviderSpec("gemini", None)
    assert [s.provider for s in chain[1:]] == ["claude", "codex", "kimi"]


def test_custom_fallback_order_is_respected() -> None:
    chain = resolve_provider_chain(
        task="flashcards",
        job_provider="kimi",
        fallback_order=("codex", "claude"),
    )
    assert [s.provider for s in chain] == ["kimi", "codex", "claude"]
