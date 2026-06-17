"""Provider→models manifest. Single source of truth for which models a user
may pick at job creation time. Keep in sync with the providers package
(`app/services/providers/__init__.py`)."""

from __future__ import annotations

MODEL_MANIFEST: dict[str, list[str]] = {
    "claude": [
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-haiku-4-5-20251001",
    ],
    "kimi": [
        "kimi-code/kimi-for-coding",
    ],
    "codex": [
        "gpt-5.5",
        "gpt-5.2",
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
    ],
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
    "opencode": [
        # opencode zen models, addressed as provider/model. The *-free ones
        # need no API key — attractive for the cheap extract phase.
        "opencode/deepseek-v4-flash-free",
        "opencode/nemotron-3-super-free",
        "opencode/mimo-v2.5-free",
        "opencode/big-pickle",
    ],
}


def is_valid(provider: str, model: str | None) -> bool:
    """True if (provider, model) is in the manifest. `model=None` is valid
    (provider-default) when the provider's list is non-empty."""
    if provider not in MODEL_MANIFEST:
        return False
    if model is None:
        return True  # caller will resolve to provider default
    return model in MODEL_MANIFEST[provider]


def default_model(provider: str) -> str | None:
    """First entry in the manifest for the provider (the recommended pick).
    Returns None for unknown providers."""
    return MODEL_MANIFEST.get(provider, [None])[0] if provider in MODEL_MANIFEST else None


# ─── Transport (cli vs api) ──────────────────────────────────────────────────
# Which providers support the API (pay-per-token SDK) transport. CLI is always
# supported; api is gated to claude/gemini for now — codex is deferred (fleet-api-5).
API_PROVIDERS: frozenset[str] = frozenset({"claude", "gemini"})  # spec §1


def api_supported(provider: str) -> bool:
    return provider in API_PROVIDERS


def validate_transport(provider: str, model: str | None, transport: str) -> str | None:
    """Return an error string if (provider, model, transport) is invalid, else None."""
    if transport not in ("cli", "api"):
        return f"unknown transport {transport!r} (expected 'cli' | 'api')"
    if transport == "api":
        if not api_supported(provider):
            return f"transport=api unsupported for provider {provider!r} (only {sorted(API_PROVIDERS)})"
        if model is None:
            return (
                "transport=api requires an explicit model (no provider-default) — "
                "it would diverge between OAuth and API-key auth"
            )
    return None


ROLE_TRANSPORTS = ("cli", "api", "inherit")


def validate_role_transport(field: str, value: str) -> str | None:
    """Return an error string if a per-role transport value is invalid, else None."""
    if value not in ROLE_TRANSPORTS:
        return f"unknown {field} {value!r} (expected 'cli' | 'api' | 'inherit')"
    return None


def resolve_role_transport(role_value: str, job_transport: str) -> str:
    """'inherit' follows the job's transport; an explicit value wins (Phase 4.1 §2)."""
    return job_transport if role_value == "inherit" else role_value
