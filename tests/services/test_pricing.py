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


def test_clodex_cached_is_subset_of_prompt_and_output_uses_ratio():
    usage = {
        "prompt_tokens": 1_000_000,
        "output_tokens": 250_000,
        "cached_tokens": 400_000,
    }
    expected = 0.6 * 0.063 + 0.25 * 0.504 + 0.4 * 0.063
    assert pricing.cost_usd("clodex", "gpt-5.6-luna", usage) == pytest.approx(expected)


def test_clodex_floor_priced_models_are_not_underreported_as_linear():
    usage = {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert pricing.cost_usd("clodex", "gpt-5.5", usage) == 0.0
    assert pricing.cost_usd("clodex", "codex-auto-review", usage) == 0.0


# ── cache-write (pricing-1b) ──────────────────────────────────────────────────

def test_gemini_cache_write_semantics_unchanged():
    """Regression: gemini prompt-includes-cached billing is untouched.

    1M prompt incl. 400k cached, 200k output on gemini-2.5-flash:
      uncached_input = 600k → 600k × $0.30/Mtok = $0.18
      output         = 200k → 200k × $2.50/Mtok = $0.50
      cache_read     = 400k → 400k × $0.03/Mtok = $0.012
      total = $0.692
    No cache_write key on gemini → contributes $0, no KeyError.
    """
    usage = {
        "prompt_tokens": 1_000_000,
        "output_tokens": 200_000,
        "cached_tokens": 400_000,
        "cache_creation_tokens": 50_000,  # present in row but must be ignored for gemini
    }
    expected = (600_000 * 0.30 + 200_000 * 2.50 + 400_000 * 0.03) / 1_000_000
    assert pricing.cost_usd("gemini", "gemini-2.5-flash", usage) == pytest.approx(expected)


def test_claude_no_cache_creation_tokens_unchanged():
    """Regression: claude row with no cache_creation_tokens bills exactly as before.

    1M prompt (uncached, disjoint), 500k cached, 200k output on claude-sonnet-4-6:
      input      = 1M  × $3.00/Mtok = $3.00
      cache_read = 500k × $0.30/Mtok = $0.15
      output     = 200k × $15.0/Mtok = $3.00
      total = $6.15
    """
    usage = {
        "prompt_tokens": 1_000_000,
        "output_tokens": 200_000,
        "cached_tokens": 500_000,
        # no cache_creation_tokens key
    }
    expected = (1_000_000 * 3.0 + 200_000 * 15.0 + 500_000 * 0.30) / 1_000_000
    assert pricing.cost_usd("claude", "claude-sonnet-4-6", usage) == pytest.approx(expected)


def test_claude_cache_creation_tokens_adds_premium():
    """New: claude cache_creation_tokens billed at 1.25 × input rate.

    On claude-sonnet-4-6 (input=$3/Mtok → cache_write=$3.75/Mtok):
      300k cache_creation → 300k × $3.75/Mtok = $1.125 extra
    Base: 1M prompt × $3 + 0 output + 0 cached = $3.00
    Total = $4.125
    """
    usage = {
        "prompt_tokens": 1_000_000,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_tokens": 300_000,
    }
    cache_write_rate = 3.0 * 1.25  # $3.75/Mtok
    expected = (1_000_000 * 3.0 + 300_000 * cache_write_rate) / 1_000_000
    assert pricing.cost_usd("claude", "claude-sonnet-4-6", usage) == pytest.approx(expected)


def test_provider_without_cache_write_key_never_raises():
    """A PRICE_MAP entry without cache_write (e.g. gemini) never KeyErrors
    and contributes $0 for cache_creation_tokens."""
    usage = {
        "prompt_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_tokens": 999_999,
    }
    # gemini-2.5-flash has no cache_write key; should return $0 for tokens above
    cost = pricing.cost_usd("gemini", "gemini-2.5-flash", usage)
    assert cost == pytest.approx(0.0)
