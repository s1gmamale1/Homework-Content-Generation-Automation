"""Tests for the CLI-concurrency semaphore knob.

Verifies that:
  (a) agent_max_concurrency is the live knob the semaphore reads.
  (b) gemini_max_concurrency is the deprecated fallback used when
      agent_max_concurrency is left at its default (8).
"""
import pytest

from app.config import settings
from app.services import agent


def test_semaphore_reads_agent_max_concurrency(monkeypatch):
    """agent_max_concurrency=3 → semaphore size 3 (live knob wins)."""
    monkeypatch.setattr(settings, "agent_max_concurrency", 3)
    # Also ensure gemini_max_concurrency is NOT 3, so this is unambiguous.
    monkeypatch.setattr(settings, "gemini_max_concurrency", 8)
    # Reset the lazy-init semaphore so _semaphore() rebuilds it.
    monkeypatch.setattr(agent, "_agent_semaphore", None)

    sem = agent._semaphore()
    assert sem._value == 3, (
        f"Expected semaphore size 3 (agent_max_concurrency), got {sem._value}"
    )


def test_semaphore_falls_back_to_gemini_max_concurrency(monkeypatch):
    """When agent_max_concurrency is at its default (8), gemini_max_concurrency wins."""
    monkeypatch.setattr(settings, "agent_max_concurrency", 8)  # default — fallback path
    monkeypatch.setattr(settings, "gemini_max_concurrency", 5)
    monkeypatch.setattr(agent, "_agent_semaphore", None)

    sem = agent._semaphore()
    assert sem._value == 5, (
        f"Expected semaphore size 5 (gemini_max_concurrency fallback), got {sem._value}"
    )
