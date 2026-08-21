"""Static per-(provider, model) price map + ``cost_usd``.

Rates are US dollars per 1,000,000 tokens. All entries (claude + gemini)
are VERIFIED against the providers' published pricing as of 2026-06-11.
cli-served calls are "free" to us (no pay-per-token), so providers/models
with no entry resolve to $0.

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

# (provider, model) → {"input": $/Mtok, "output": $/Mtok, "cache_read": $/Mtok,
#                       "cache_write": $/Mtok}
# cache_write = 1.25 × input (Anthropic cache_creation_input_tokens rate).
# Gemini entries intentionally have NO cache_write key — .get("cache_write", 0.0)
# in cost_usd makes the missing key contribute $0, no error.
PRICE_MAP: dict[tuple[str, str], dict[str, float]] = {
    # ─── Claude (VERIFIED) ────────────────────────────────────────────────
    # as of 2026-06-11, source: Anthropic pricing
    # (platform.claude.com/docs/en/about-claude/models/overview)
    ("claude", "claude-opus-4-8"): {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    ("claude", "claude-opus-4-7"): {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    ("claude", "claude-sonnet-4-6"): {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    ("claude", "claude-haiku-4-5-20251001"): {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},

    # ─── Gemini (VERIFIED) ────────────────────────────────────────────────
    # as of 2026-06-11, sources (both agree): ai.google.dev/gemini-api/docs/pricing
    # + cloud.google.com/vertex-ai/generative-ai/pricing. Rates below are the
    # ≤200k-token tier; pro models double input (and raise output/cache) above
    # 200k input tokens — our per-phase prompts stay well under that, so the
    # base tier is the correct rate. Thinking tokens bill as OUTPUT (the gemini
    # CLI reports them under "thoughts"; ensure they're counted into
    # output_tokens upstream or the $ readout under-counts).
    ("gemini", "gemini-2.5-pro"): {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    ("gemini", "gemini-2.5-flash"): {"input": 0.30, "output": 2.50, "cache_read": 0.03},
    ("gemini", "gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40, "cache_read": 0.01},
    ("gemini", "gemini-3.1-pro-preview"): {"input": 2.0, "output": 12.0, "cache_read": 0.20},
    ("gemini", "gemini-3-flash-preview"): {"input": 0.50, "output": 3.0, "cache_read": 0.05},
    ("gemini", "gemini-3.1-flash-lite-preview"): {"input": 0.25, "output": 1.50, "cache_read": 0.025},

    # ─── Gemini 3.x flash (api-only, 2026-08-03 rollout — replaces retired 2.5) ──
    # Google Standard Developer API list price, ai.google.dev/gemini-api/docs/pricing, 2026-08-03
    # ai.google.dev/gemini-api/docs/pricing verified 2026-08-21: promo $0.75/$3.75/$0.075
    # through 2026-12-31, doubling to $1.50/$7.50/$0.15 on 2027-01-01 — update then.
    ("gemini", "gemini-3.7-flash"): {"input": 0.75, "output": 3.75, "cache_read": 0.075},
    ("gemini", "gemini-3.6-flash"): {"input": 0.75, "output": 3.75, "cache_read": 0.075},
    ("gemini", "gemini-3.5-flash"): {"input": 1.50, "output": 9.00, "cache_read": 0.15},
    ("gemini", "gemini-3.5-flash-lite"): {"input": 0.30, "output": 2.50, "cache_read": 0.03},

    # ─── Clodex (public /api/pricing payload, 2026-07-15) ───────────────
    # usage_fixed_price is the $/M input rate and completion_ratio multiplies
    # output. For entries with usage_floor_price, use max(fixed, floor) as a
    # conservative budget rate: the public payload does not document enough to
    # reproduce the provider's exact floor calculation, but paid usage must
    # never become $0 and bypass the app's spend caps.
    ("clodex", "gpt-5.6-luna"): {"input": 0.063, "output": 0.504, "cache_read": 0.063},
    ("clodex", "gpt-5.6-terra"): {"input": 0.070, "output": 0.560, "cache_read": 0.070},
    ("clodex", "gpt-5.6-sol"): {"input": 0.084, "output": 0.672, "cache_read": 0.084},
    ("clodex", "gpt-5.5"): {"input": 0.125, "output": 0.750, "cache_read": 0.125},
    ("clodex", "codex-auto-review"): {"input": 0.070, "output": 0.070, "cache_read": 0.070},
}

# Clodex may report a served alias different from the requested model. If a
# future alias is not yet in PRICE_MAP, budget accounting uses the highest
# currently published Clodex rates instead of the generic unpriced=$0 behavior.
_CLODEX_UNKNOWN_RATES = {"input": 0.125, "output": 0.750, "cache_read": 0.125}

# Providers whose reported prompt count INCLUDES the cached span (see the
# per-provider semantics comment in cost_usd).
_PROMPT_INCLUDES_CACHED: frozenset[str] = frozenset({"gemini", "clodex"})

# Cache-write (pricing-1b, Task 2): Anthropic bills cache WRITES at 1.25× input
# (cache_creation_input_tokens). As of this task, agent_usages.cache_creation_tokens
# carries the value (added in Task 1) and cost_usd prices it via cache_write in
# PRICE_MAP. The former under-report bias is now resolved.
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
    if rates is None and provider == "clodex":
        rates = _CLODEX_UNKNOWN_RATES
        key = (provider, resolved)
        if key not in _LOGGED_MISSING:
            _LOGGED_MISSING.add(key)
            logger.warning(
                f"pricing: unknown Clodex model {resolved!r} — using "
                "conservative fallback rates"
            )
    if rates is None:
        key = (provider, resolved)
        if key not in _LOGGED_MISSING:
            _LOGGED_MISSING.add(key)
            logger.info(f"pricing: no $ entry for ({provider!r}, {resolved!r}) — billing $0")
        return 0.0

    cached = int(usage.get("cached_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    output = int(usage.get("output_tokens") or 0)

    # Cached-token semantics differ PER PROVIDER (both verified 2026-06-11):
    #   claude: prompt_tokens mirrors Anthropic input_tokens — the UNCACHED
    #           count, DISJOINT from cached_tokens. Bill prompt as-is.
    #   gemini/clodex: prompt count INCLUDES cached tokens — billable
    #           input is prompt - cached, else the cached span double-bills
    #           (input rate + cache-read rate).
    if provider in _PROMPT_INCLUDES_CACHED:
        uncached_input = max(prompt - cached, 0)  # clamp malformed rows
    else:
        uncached_input = prompt

    input_cost = uncached_input * rates["input"] / 1_000_000
    output_cost = output * rates["output"] / 1_000_000
    cache_cost = cached * rates["cache_read"] / 1_000_000
    cache_creation = int(usage.get("cache_creation_tokens") or 0)
    cache_write_cost = cache_creation * rates.get("cache_write", 0.0) / 1_000_000
    return input_cost + output_cost + cache_cost + cache_write_cost
