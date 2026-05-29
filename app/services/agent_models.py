"""Provider→models manifest. Single source of truth for which models a user
may pick at job creation time. Keep in sync with the providers package
(`app/services/providers/__init__.py`)."""

from __future__ import annotations

MODEL_MANIFEST: dict[str, list[str]] = {
    "claude": [
        "claude-opus-4-7",
        "claude-sonnet-4-6",
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
