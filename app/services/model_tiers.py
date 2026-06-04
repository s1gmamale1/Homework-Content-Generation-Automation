"""Cross-provider capability tiers + tier-up judge selection.

Pure, no I/O. A validation judge must be at least as capable as the model that
produced the output, so we pick the judge from the tier ABOVE the generator's.
Designates are intentionally NON-CLAUDE so validation never draws down the
scarce claude Max pool (mirrors the resilience effort's provider isolation).
Tier placement is a judgment call over partly-future models — kept here as data
so it re-tunes without touching logic.
"""

from __future__ import annotations

from typing import Optional

from app.services.agent_models import default_model

# 1 = strongest. Every MODEL_MANIFEST model appears exactly once.
_MODEL_TIER: dict[str, int] = {
    # Tier 1 — Frontier
    "claude-opus-4-7": 1,
    "gpt-5.5": 1,
    "gemini-3.1-pro-preview": 1,
    # Tier 2 — Strong
    "claude-sonnet-4-6": 2,
    "gpt-5.2": 2,
    "gpt-5": 2,
    "gemini-3-flash-preview": 2,
    "gemini-2.5-pro": 2,
    # Tier 3 — Mid
    "claude-haiku-4-5-20251001": 3,
    "gpt-5-mini": 3,
    "gemini-3.1-flash-lite-preview": 3,
    "gemini-2.5-flash": 3,
    "kimi-code/kimi-for-coding": 3,
    # Tier 4 — Light
    "gpt-5-nano": 4,
    "gemini-2.5-flash-lite": 4,
    "opencode/deepseek-v4-flash-free": 4,
    "opencode/nemotron-3-super-free": 4,
    "opencode/mimo-v2.5-free": 4,
    "opencode/big-pickle": 4,
}

# Unknown model (shouldn't happen — manifest is enforced at /generate) -> assume
# Strong, so the judge errs toward a Frontier check rather than under-grading.
_DEFAULT_TIER = 2

# Judge designate per JUDGE tier: (primary, alternate). Both non-claude. The
# alternate is used when the primary would equal the generating model (no-self).
_JUDGE_DESIGNATES: dict[int, tuple[tuple[str, str], tuple[str, str]]] = {
    1: (("gemini", "gemini-3.1-pro-preview"), ("codex", "gpt-5.5")),
    2: (("gemini", "gemini-2.5-pro"), ("codex", "gpt-5")),
    3: (("gemini", "gemini-2.5-flash"), ("codex", "gpt-5-mini")),
}


def tier_of(provider: str, model: Optional[str]) -> int:
    """Capability tier (1=strongest) of (provider, model). `model=None` resolves
    to the provider's default model first."""
    resolved = model or default_model(provider)
    return _MODEL_TIER.get(resolved or "", _DEFAULT_TIER)


def judge_model_for(gen_provider: str, gen_model: Optional[str]) -> tuple[str, str]:
    """Pick the judge (provider, model) one tier above the generator. Clamps at
    tier 1 (a top-tier generator gets a tier-1 peer). Falls back to the alternate
    designate if the primary would be the generating model itself (no-self)."""
    gen_tier = tier_of(gen_provider, gen_model)
    judge_tier = max(1, gen_tier - 1)
    primary, alternate = _JUDGE_DESIGNATES[judge_tier]
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    return alternate if primary == resolved_gen else primary
