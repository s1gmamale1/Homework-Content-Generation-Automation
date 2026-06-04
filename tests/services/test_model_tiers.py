"""Unit tests for ``app.services.model_tiers``.

Coverage:
- Every manifest model gets a tier assignment.
- None model resolves to provider default, then its tier.
- Judge is one tier up and never claude.
- Top-tier generator gets non-self peer.
- Collision avoidance (primary == generator falls back to alternate).
- Light generator jumps multiple tiers (tier 4 → tier 3 judge).
"""

from __future__ import annotations

import pytest

from app.services.agent_models import MODEL_MANIFEST
from app.services import model_tiers as mt


def test_every_manifest_model_has_a_tier():
    """All models in MODEL_MANIFEST must have a tier assignment."""
    for provider, models in MODEL_MANIFEST.items():
        for model in models:
            t = mt.tier_of(provider, model)
            assert t in (1, 2, 3, 4), f"{provider}/{model} -> {t}"


def test_none_model_resolves_to_provider_default_tier():
    """model=None resolves to provider default, then its tier."""
    assert mt.tier_of("gemini", None) == mt.tier_of(
        "gemini", "gemini-3.1-pro-preview"
    )
    assert mt.tier_of("codex", None) == mt.tier_of("codex", "gpt-5.5")


def test_judge_is_one_tier_up_and_never_claude():
    """Judge must be non-claude and at least one tier stronger."""
    jp, jm = mt.judge_model_for("claude", "claude-sonnet-4-6")
    assert jp != "claude"
    assert mt.tier_of(jp, jm) == 1


def test_top_tier_generator_judged_by_non_self_peer():
    """Top-tier generator (clamped at tier 1) gets a tier-1 peer that isn't itself."""
    jp, jm = mt.judge_model_for("claude", "claude-opus-4-7")
    assert (jp, jm) != ("claude", "claude-opus-4-7")
    assert jp != "claude"
    assert mt.tier_of(jp, jm) == 1


def test_collision_falls_back_to_alternate():
    """If primary designate is the generator, use alternate."""
    jp, jm = mt.judge_model_for("gemini", "gemini-3.1-pro-preview")
    assert (jp, jm) != ("gemini", "gemini-3.1-pro-preview")


def test_light_generator_jumps_to_mid():
    """Tier-4 generator (gpt-5-nano) gets a tier-3 judge."""
    jp, jm = mt.judge_model_for("codex", "gpt-5-nano")
    assert mt.tier_of(jp, jm) == 3
