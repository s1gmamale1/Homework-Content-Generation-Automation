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
    # Codex-CLI models. Source of truth = the codex CLI's own model list
    # (~/.codex/models_cache.json, visibility="list") for client 0.144.0.
    # gpt-5.5 stays first = the CLI's configured default (config.toml model=).
    # codex is CLI-only (not in API_PROVIDERS) — these run over transport=cli.
    "codex": [
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex-spark",
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
    # Clodex OpenAI-compatible text models. Live /v1/models probe 2026-07-15.
    # gpt-image-2 is intentionally excluded from this text-agent manifest.
    "clodex": [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "codex-auto-review",
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
# supported except for explicitly API-only providers.
API_PROVIDERS: frozenset[str] = frozenset({"claude", "gemini", "clodex"})
API_ONLY_PROVIDERS: frozenset[str] = frozenset({"clodex"})


def api_supported(provider: str) -> bool:
    return provider in API_PROVIDERS


def validate_transport(provider: str, model: str | None, transport: str) -> str | None:
    """Return an error string if (provider, model, transport) is invalid, else None."""
    if transport not in ("cli", "api"):
        return f"unknown transport {transport!r} (expected 'cli' | 'api')"
    if transport == "cli" and provider in API_ONLY_PROVIDERS:
        return f"provider {provider!r} is api-only (transport=cli is unsupported)"
    if transport == "api":
        if not api_supported(provider):
            return f"transport=api unsupported for provider {provider!r} (only {sorted(API_PROVIDERS)})"
        if model is None:
            return (
                "transport=api requires an explicit model (no provider-default) — "
                "it would diverge between OAuth and API-key auth"
            )
    return None


def validate_role_provider(role: str, provider: str) -> str | None:
    """Reject provider/role combinations the pipeline cannot execute safely."""
    if role == "extract" and provider in API_ONLY_PROVIDERS:
        return (
            f"extract provider {provider!r} is unsupported: extraction vision "
            "fallbacks currently require a CLI-capable provider"
        )
    return None


ROLE_TRANSPORTS = ("cli", "api", "inherit")


def validate_role_transport(field: str, value: str) -> str | None:
    """Return an error string if a per-role transport value is invalid, else None."""
    if value not in ROLE_TRANSPORTS:
        return f"unknown {field} {value!r} (expected 'cli' | 'api' | 'inherit')"
    return None


# ─── Output Language ───────────────────────────────────────────────────────────
OUTPUT_LANGUAGES = frozenset({"uz", "en", "ru"})


def validate_output_language(value, *, allow_none: bool):
    """Return an error string if output_language is invalid, else None."""
    if value is None:
        return None if allow_none else "output_language is required"
    if value not in OUTPUT_LANGUAGES:
        return f"output_language must be one of {sorted(OUTPUT_LANGUAGES)}; got {value!r}"
    return None


def resolve_output_language(explicit, default: str) -> str:
    """Resolve output_language: explicit value or default fallback."""
    return explicit or default


def resolve_output_language_for_book(explicit, book_source_language: str, global_default: str) -> str:
    """Output language precedence: explicit operator pick → book's source
    language → global launch default."""
    return explicit or book_source_language or global_default


def resolve_role_transport(role_value: str, job_transport: str) -> str:
    """'inherit' follows the job's transport; an explicit value wins (Phase 4.1 §2)."""
    return job_transport if role_value == "inherit" else role_value


def resolve_role_selection(
    explicit_provider: str | None,
    explicit_model: str | None,
    default_provider: str,
    default_model_: str | None,
) -> tuple[str, str | None]:
    """Resolve a role's (provider, model) at launch time.

    Explicit provider wins: its model is the explicit pick or THAT provider's
    own default (never the global default's model, which belongs to a different
    provider). Auto provider -> the global default pair verbatim.
    """
    if explicit_provider is not None:
        return explicit_provider, (explicit_model or default_model(explicit_provider))
    return default_provider, default_model_


def resolve_role_transport_default(explicit_transport: str, default_transport: str) -> str:
    """'inherit' (the launcher 'Auto') -> the global default transport (which may
    itself be 'inherit'); an explicit 'cli'/'api' wins."""
    return default_transport if explicit_transport == "inherit" else explicit_transport


SESSION_LIMIT_STRATEGIES = ("pause", "switch", "inherit")


def validate_session_limit_strategy(value: str) -> str | None:
    """Return an error string if session_limit_strategy is invalid, else None."""
    if value not in SESSION_LIMIT_STRATEGIES:
        return (
            f"unknown session_limit_strategy {value!r} "
            f"(expected 'pause' | 'switch' | 'inherit')"
        )
    return None


def resolve_session_limit_strategy(batch_value: str | None) -> str:
    """Return the effective session-limit strategy for a batch.

    'pause' or 'switch' win as-is (explicit per-batch override).
    'inherit' or None fall back to ``settings.session_limit_strategy``
    (the operator's fleet-wide default).
    """
    from app.config import settings

    if batch_value in ("pause", "switch"):
        return batch_value
    return settings.session_limit_strategy
