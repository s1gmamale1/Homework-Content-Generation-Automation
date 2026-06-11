"""Static per-(provider, model) price map + ``cost_usd``.

Rates are US dollars per 1,000,000 tokens. The cost logic here is the
deliverable; the gemini numbers are operator-confirmable data (see the
TODO in the gemini section). cli-served calls are "free" to us (no
pay-per-token), so providers/models with no entry resolve to $0.

Billing semantics (verified against the claude-api skill, 2026-06-11):
``agent_usages.prompt_tokens`` mirrors Anthropic's ``input_tokens`` — the
UNCACHED input count, which is DISJOINT from ``cached_tokens`` (Anthropic's
``cache_read_input_tokens``). Total prompt size = prompt_tokens +
cached_tokens + cache_creation. So we bill ``prompt_tokens`` at the input
rate and ``cached_tokens`` at the cache-read rate with NO subtraction.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.services import agent_models

# (provider, model) → {"input": $/Mtok, "output": $/Mtok, "cache_read": $/Mtok}
PRICE_MAP: dict[tuple[str, str], dict[str, float]] = {
    # ─── Claude (VERIFIED) ────────────────────────────────────────────────
    # as of 2026-06-11, source: Anthropic pricing
    # (platform.claude.com/docs/en/about-claude/models/overview)
    ("claude", "claude-opus-4-8"): {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    ("claude", "claude-opus-4-7"): {"input": 5.0, "output": 25.0, "cache_read": 0.50},
    ("claude", "claude-sonnet-4-6"): {"input": 3.0, "output": 15.0, "cache_read": 0.30},
    ("claude", "claude-haiku-4-5-20251001"): {"input": 1.0, "output": 5.0, "cache_read": 0.10},

    # ─── Gemini ───────────────────────────────────────────────────────────
    # TODO(pricing): confirm before relying on the $ readout. The gemini-2.5
    # family is anchored to published Google AI pricing; the gemini-3.x preview
    # models are best-effort tier estimates (preview pricing is unstable) — all
    # tagged VERIFY. cache_read estimated at ~0.25× input where unknown.
    # as of 2026-06-11, source: ai.google.dev/gemini-api/docs/pricing — VERIFY
    ("gemini", "gemini-2.5-pro"): {"input": 1.0, "output": 10.0, "cache_read": 0.25},  # VERIFY
    ("gemini", "gemini-2.5-flash"): {"input": 0.30, "output": 2.50, "cache_read": 0.075},  # VERIFY
    ("gemini", "gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40, "cache_read": 0.025},  # VERIFY
    # gemini-3.x preview models — best-effort estimates, NOT confirmed pricing.
    # as of 2026-06-11, source: ai.google.dev/gemini-api/docs/pricing — VERIFY
    ("gemini", "gemini-3.1-pro-preview"): {"input": 1.25, "output": 10.0, "cache_read": 0.31},  # VERIFY
    ("gemini", "gemini-3-flash-preview"): {"input": 0.30, "output": 2.50, "cache_read": 0.075},  # VERIFY
    ("gemini", "gemini-3.1-flash-lite-preview"): {"input": 0.10, "output": 0.40, "cache_read": 0.025},  # VERIFY
}

# Models we've already logged as missing, so the warning fires once per gap.
_LOGGED_MISSING: set[tuple[str, Optional[str]]] = set()


def cost_usd(provider: str, model: Optional[str], usage: dict[str, Any]) -> float:
    """Dollar cost of one usage row. Returns 0.0 for cli-free / unpriced rows.

    ``model=None`` resolves to the provider's default model (NOT $0) — a row
    recorded with a provider-default would otherwise silently price at zero.
    """
    # model=None defense: resolve to the provider default before lookup.
    resolved = model or agent_models.default_model(provider)
    rates = PRICE_MAP.get((provider, resolved)) if resolved is not None else None
    if rates is None:
        key = (provider, resolved)
        if key not in _LOGGED_MISSING:
            _LOGGED_MISSING.add(key)
            logger.info(f"pricing: no $ entry for ({provider!r}, {resolved!r}) — billing $0")
        return 0.0

    cached = int(usage.get("cached_tokens") or 0)
    uncached_input = int(usage.get("prompt_tokens") or 0)  # disjoint from cached
    output = int(usage.get("output_tokens") or 0)

    input_cost = uncached_input * rates["input"] / 1_000_000
    output_cost = output * rates["output"] / 1_000_000
    cache_cost = cached * rates["cache_read"] / 1_000_000
    return input_cost + output_cost + cache_cost
