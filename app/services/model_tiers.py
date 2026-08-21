"""Judge model selection (+ a capability-tier table kept for reference).

A validation judge must be at least as capable as the model that produced the
output. The job's stamped ``judge_provider`` / ``judge_model`` (written at
launch from the launch_defaults DB row) is the primary selection; the auto-tier
path (``judge_model_for``) provides the self-grade fallback — a strong frontier
peer that is guaranteed non-self for ANY generator.

`tier_of` / `_MODEL_TIER` are retained for reference + the manifest-completeness
test; judge selection no longer depends on them.

Every current MODEL_MANIFEST model appears here exactly once, PLUS the
retired gemini-2.5 family (`agent_models.RETIRED_GEMINI_MODELS`) — kept so a
historical job whose stamped judge/generator is a 2.5 model still resolves a
real tier instead of silently falling back to `_DEFAULT_TIER`.
"""

from __future__ import annotations

from typing import Optional

from app.services.agent_models import default_model

# 1 = strongest. Every MODEL_MANIFEST model appears exactly once.
_MODEL_TIER: dict[str, int] = {
    # Tier 1 — Frontier
    "claude-opus-4-7": 1,
    "gpt-5.5": 1,
    "gpt-5.6-sol": 1,
    "gemini-3.1-pro-preview": 1,
    # Tier 2 — Strong
    "claude-sonnet-4-6": 2,
    "gpt-5.6-terra": 2,
    "codex-auto-review": 2,
    "gpt-5.4": 2,
    "gemini-3-flash-preview": 2,
    "gemini-3.7-flash": 2,
    "gemini-3.6-flash": 2,
    "gemini-3.5-flash": 2,
    "gemini-2.5-pro": 2,  # retired from MODEL_MANIFEST — kept for historical attribution
    # Tier 3 — Mid
    "claude-haiku-4-5-20251001": 3,
    "gpt-5.6-luna": 3,
    "gemini-3.1-flash-lite-preview": 3,
    "gemini-2.5-flash": 3,  # retired from MODEL_MANIFEST — kept for historical attribution
    "kimi-code/kimi-for-coding": 3,
    # Tier 4 — Light
    "gpt-5.4-mini": 4,
    "gpt-5.3-codex-spark": 4,
    "gemini-3.5-flash-lite": 4,
    "gemini-2.5-flash-lite": 4,  # retired from MODEL_MANIFEST — kept for historical attribution
    "opencode/deepseek-v4-flash-free": 4,
    "opencode/nemotron-3-super-free": 4,
    "opencode/mimo-v2.5-free": 4,
    "opencode/big-pickle": 4,
}

# Unknown model (shouldn't happen — manifest is enforced at /generate) -> assume
# Strong, so the judge errs toward a Frontier check rather than under-grading.
_DEFAULT_TIER = 2

# No-self rule: when the configured judge model IS the generator, grade with a
# different strong peer so a model never grades its own output. A *fixed* fallback
# constant can't satisfy this — whatever model it is, a generator equal to it
# self-matches again. So the fallback is generator-AWARE: two distinct frontier
# peers, returning whichever is NOT the generator. claude-opus-4-7 is the strongest
# peer (and the documented default judge, config.py:111-112); gemini-3.1-pro-preview
# is the strongest non-claude peer and reliably on PATH. The result is non-self for
# ANY generator (the two peers are distinct). On a single-provider worker lacking the
# chosen peer's creds this rare path degrades that one judge call to "unavailable"
# (safe — never blocks the job).
_PRIMARY_SELF_FALLBACK: tuple[str, str] = ("claude", "claude-opus-4-7")
_ALT_SELF_FALLBACK: tuple[str, str] = ("gemini", "gemini-3.1-pro-preview")


def _self_fallback(resolved_gen: tuple[str, str]) -> tuple[str, str]:
    """A strong frontier peer guaranteed != ``resolved_gen`` (the no-self rule).
    Returns the alternate only when the generator IS the primary peer, so the
    result is non-self for ANY generator (the two peers are distinct)."""
    return _ALT_SELF_FALLBACK if resolved_gen == _PRIMARY_SELF_FALLBACK else _PRIMARY_SELF_FALLBACK


def tier_of(provider: str, model: Optional[str]) -> int:
    """Capability tier (1=strongest) of (provider, model). `model=None` resolves
    to the provider's default model first. (Reference only — judge selection no
    longer uses this.)"""
    resolved = model or default_model(provider)
    return _MODEL_TIER.get(resolved or "", _DEFAULT_TIER)


def judge_model_for(gen_provider: str, gen_model: Optional[str]) -> tuple[str, str]:
    """Self-grade fallback judge (provider, model): a strong frontier peer
    guaranteed != the generator. Reached only when a job's explicit judge would
    grade its own output, or (defensively) when no judge is stamped — in both
    cases the safe answer is a non-self frontier peer, not any configured default
    (the configured default now lives in the launch_defaults DB row, applied at
    launch time, never here)."""
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    return _self_fallback(resolved_gen)


def resolve_judge(
    gen_provider: str,
    gen_model: Optional[str],
    judge_provider: Optional[str],
    judge_model: Optional[str],
) -> tuple[str, str]:
    """Effective judge (provider, model). Explicit override wins, EXCEPT a
    self-grade (judge resolves to the same model as the generator) which is
    hard-swapped to the safe auto-tier judge. A NULL override falls back to the
    auto-tier judge.

    Both sides' models are resolved (an Auto/None model -> the provider's
    default) BEFORE comparison: a raw `judge_model` comparison let a same-provider
    judge with model=None (the FE's default for a cli judge) slip past the check
    and silently self-grade. The self-grade fallback is `judge_model_for`, which uses
    the generator-aware `_self_fallback` (a frontier peer guaranteed non-self for ANY
    generator) rather than a fixed constant that could itself self-match the
    generator."""
    if judge_provider is None:
        return judge_model_for(gen_provider, gen_model)
    resolved_gen = (gen_provider, gen_model or default_model(gen_provider))
    resolved_judge = (judge_provider, judge_model or default_model(judge_provider))
    if resolved_judge == resolved_gen:
        return judge_model_for(gen_provider, gen_model)
    return (judge_provider, judge_model)


def resolve_solver(
    gen_provider: str,
    gen_model: Optional[str],
    solver_provider_ov: Optional[str],
    solver_model_ov: Optional[str],
) -> tuple[str, str]:
    """Resolve the solver (provider, model): an explicit override wins unless it
    would let the generator's own model re-solve its own key (self-grade), which is
    swapped to a generator-aware frontier peer. Identical policy to resolve_judge —
    a solver, like a judge, must out-reason the producer. See _self_fallback."""
    return resolve_judge(gen_provider, gen_model, solver_provider_ov, solver_model_ov)
