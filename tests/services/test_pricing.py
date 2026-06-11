"""Unit tests for the static price map + cost_usd (DB-free, pure)."""

from __future__ import annotations

import pytest

from app.services import pricing


def test_opus_input_plus_output_anchor():
    # Verified anchor: Opus 4.8 is $5/Mtok in + $25/Mtok out (NOT the deprecated
    # $15/$75). 1M in + 1M out → 5.0 + 25.0 == 30.0.
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_tokens": 0}
    assert pricing.cost_usd("claude", "claude-opus-4-8", usage) == 30.0


def test_opus_cache_read_billed_at_cache_rate():
    # cached_tokens are billed at the cache-read rate ($0.50/Mtok for opus);
    # prompt_tokens is the UNCACHED count billed at input rate ($5/Mtok). They
    # are disjoint (Anthropic's input_tokens excludes cache_read), so NO subtraction.
    usage = {
        "prompt_tokens": 1_000_000,   # uncached input → $5.00
        "output_tokens": 0,
        "cached_tokens": 1_000_000,   # cache-read → $0.50
    }
    assert pricing.cost_usd("claude", "claude-opus-4-8", usage) == 5.50


def test_sonnet_pricing():
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_tokens": 0}
    # sonnet 4.6: $3 in + $15 out
    assert pricing.cost_usd("claude", "claude-sonnet-4-6", usage) == 18.0


def test_kimi_has_no_price_returns_zero():
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_tokens": 0}
    assert pricing.cost_usd("kimi", "kimi-code/kimi-for-coding", usage) == 0.0


def test_unknown_model_returns_zero_without_crashing():
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 0, "cached_tokens": 0}
    assert pricing.cost_usd("claude", "nonexistent-model", usage) == 0.0


def test_model_none_resolves_to_provider_default_not_zero():
    # model=None must NOT silently return $0 — it resolves to the provider's
    # default model (agent_models.default_model("claude") → claude-sonnet-4-6)
    # and prices that. 1M in at $3/Mtok = 3.0.
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 0, "cached_tokens": 0}
    cost = pricing.cost_usd("claude", None, usage)
    assert cost == 3.0  # sonnet default's input rate, not 0


def test_empty_usage_is_zero():
    assert pricing.cost_usd("claude", "claude-opus-4-8", {}) == 0.0


def test_gemini_cached_is_subset_of_prompt_no_double_bill():
    """Gemini's promptTokenCount INCLUDES cachedContentTokenCount (verified
    against Google's usageMetadata billing semantics 2026-06-11) — billable
    input = prompt - cached. Claude's prompt_tokens is DISJOINT from cached.
    1M prompt incl. 400k cached on 3.1-pro: 600k×$2 + 400k×$0.20 = $1.28."""
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 0, "cached_tokens": 400_000}
    cost = pricing.cost_usd("gemini", "gemini-3.1-pro-preview", usage)
    assert abs(cost - (0.6 * 2.0 + 0.4 * 0.20)) < 1e-9


def test_gemini_cached_never_negative_input():
    # Defensive clamp: malformed rows where cached > prompt must not bill
    # negative input.
    usage = {"prompt_tokens": 100, "output_tokens": 0, "cached_tokens": 500}
    cost = pricing.cost_usd("gemini", "gemini-2.5-flash", usage)
    assert cost == pytest.approx(500 * 0.03 / 1_000_000)
