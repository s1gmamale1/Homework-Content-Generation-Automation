"""Manifest hygiene: only models the CLIs actually have may be offerable."""
from app.services.agent_models import (
    API_ONLY_PROVIDERS,
    GEMINI_API_ONLY_MODELS,
    MODEL_MANIFEST,
    RETIRED_GEMINI_MODELS,
    api_supported,
    default_model,
    is_valid,
    validate_transport,
)


def test_phantom_gemini_3_5_flash_now_registered_api_only():
    # SELECTION test, UPDATED (2026-08-03, gemini-3.x-flash rollout): the
    # original finding stands — gemini-3.5-flash does NOT exist in the gemini
    # CLI's model catalog (ModelNotFoundError, verified live). What changed is
    # that it is now a REAL model reachable through the plain API key, so this
    # task registers it as api-only rather than excluding it from the manifest
    # entirely: offerable (is_valid True) but transport=cli is rejected.
    assert "gemini-3.5-flash" in MODEL_MANIFEST["gemini"]
    assert is_valid("gemini", "gemini-3.5-flash") is True
    assert "api-only" in (validate_transport("gemini", "gemini-3.5-flash", "cli") or "")


def test_real_gemini_models_still_valid():
    # The manifest gemini models (3.x previews + the new 3.x flash api-only
    # trio) all resolve OK.
    for m in ("gemini-3.1-pro-preview", "gemini-3-flash-preview",
              "gemini-3.1-flash-lite-preview", "gemini-3.6-flash",
              "gemini-3.5-flash", "gemini-3.5-flash-lite"):
        assert is_valid("gemini", m) is True, m


def test_gemini_2_5_retired_from_manifest():
    # SELECTION test (new, 2026-08-03): the gemini-2.5 family 404s on the
    # plain API key and is retired from the offerable manifest — is_valid
    # must now be False for all three. Their price/tier rows are KEPT
    # (historical-attribution ACCOUNTING data, unaffected by this change —
    # see test_pricing.py / test_model_tiers.py).
    for m in RETIRED_GEMINI_MODELS:
        assert m not in MODEL_MANIFEST["gemini"], m
        assert is_valid("gemini", m) is False, m


def test_retired_gemini_models_set_is_exact():
    assert RETIRED_GEMINI_MODELS == frozenset(
        {"gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"}
    )


def test_gemini_3x_flash_trio_is_api_only():
    assert GEMINI_API_ONLY_MODELS == frozenset(
        {"gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"}
    )
    for m in GEMINI_API_ONLY_MODELS:
        assert is_valid("gemini", m) is True, m
        assert "api-only" in (validate_transport("gemini", m, "cli") or ""), m
        assert validate_transport("gemini", m, "api") is None, m


def test_clodex_live_text_catalog_and_transport_contract():
    assert MODEL_MANIFEST["clodex"] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "codex-auto-review",
    ]
    assert "gpt-image-2" not in MODEL_MANIFEST["clodex"]
    assert default_model("clodex") == "gpt-5.6-sol"
    assert api_supported("clodex") is True
    assert API_ONLY_PROVIDERS == frozenset({"clodex"})
    assert validate_transport("clodex", "gpt-5.6-luna", "api") is None


def test_clodex_requires_api_and_explicit_model():
    assert "api-only" in (validate_transport("clodex", "gpt-5.6-luna", "cli") or "")
    assert "explicit model" in (validate_transport("clodex", None, "api") or "")
