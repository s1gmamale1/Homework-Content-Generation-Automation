from app.services import model_tiers as mt

import pytest

from app.config import settings

# Real ids from _MODEL_TIER:
#   tier 1 (strongest): claude/claude-opus-4-7, gemini/gemini-3.1-pro-preview
#   tier 2 (weaker):    gemini/gemini-2.5-pro, claude/claude-sonnet-4-6
# gemini's default model (agent_models.default_model) is gemini-3.1-pro-preview.
_GEN_PROV, _GEN_MODEL = "gemini", "gemini-2.5-pro"        # tier 2
_JUDGE_PROV, _JUDGE_MODEL = "claude", "claude-opus-4-7"   # tier 1, different from gen


def test_explicit_judge_wins():
    assert mt.resolve_judge(_GEN_PROV, _GEN_MODEL, _JUDGE_PROV, _JUDGE_MODEL) == (
        _JUDGE_PROV, _JUDGE_MODEL,
    )


def test_null_override_uses_auto_tier():
    assert mt.resolve_judge(_GEN_PROV, _GEN_MODEL, None, None) == mt.judge_model_for(
        _GEN_PROV, _GEN_MODEL,
    )


def test_exact_self_grade_is_swapped_to_a_non_self_judge():
    # explicit judge == generator -> must NOT grade its own output. The swap target
    # is the safe auto-tier judge (guaranteed non-self), NOT the raw fallback constant.
    result = mt.resolve_judge(_GEN_PROV, _GEN_MODEL, _GEN_PROV, _GEN_MODEL)
    assert result != (_GEN_PROV, _GEN_MODEL)                      # never the generator
    assert result == mt.judge_model_for(_GEN_PROV, _GEN_MODEL)


def test_null_judge_model_same_provider_is_not_self_grade(monkeypatch):
    # REGRESSION: a gemini generator (explicit) + a gemini judge with Auto/None
    # model must NOT self-grade. None resolves to the provider default BEFORE the
    # equality check. Pin the default judge so the assertion is config-independent.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.resolve_judge("gemini", "gemini-3.1-pro-preview", "gemini", None)
    assert result != ("gemini", "gemini-3.1-pro-preview")        # not the generator
    assert result != ("gemini", None)                            # not an ambiguous self
    assert result == ("claude", "claude-opus-4-7")               # the non-gemini peer


def test_self_fallback_holds_when_default_judge_is_gemini_3_1(monkeypatch):
    # Even when the DEFAULT judge IS gemini-3.1-pro-preview, a gemini-3.1-pro-preview
    # generator must not grade itself → the claude peer.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.judge_model_for("gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_both_auto_same_provider_is_not_self_grade():
    # generator on gemini default + judge on gemini default -> still independent.
    result = mt.resolve_judge("gemini", None, "gemini", None)
    assert result != ("gemini", None)
    assert result == mt.judge_model_for("gemini", None)


def test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator(monkeypatch):
    # resolve_judge with an explicit judge == the gemini-3.1 generator must swap to
    # the non-gemini peer, regardless of the configured default judge.
    monkeypatch.setattr(settings, "judge_provider", "gemini")
    monkeypatch.setattr(settings, "judge_model", "gemini-3.1-pro-preview")
    result = mt.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_self_fallback_is_non_self_for_a_claude_opus_generator(monkeypatch):
    # THE OPTION-B FIX: a claude-opus-4-7 generator under the DEFAULT claude-opus-4-7
    # judge must NOT self-grade. A single fixed claude-opus fallback would return the
    # generator's own model here; the generator-aware fallback must return the
    # distinct alternate peer instead.
    monkeypatch.setattr(settings, "judge_provider", "claude")
    monkeypatch.setattr(settings, "judge_model", "claude-opus-4-7")
    result = mt.judge_model_for("claude", "claude-opus-4-7")
    assert result != ("claude", "claude-opus-4-7")               # not the generator
    assert result == ("gemini", "gemini-3.1-pro-preview")        # the alternate peer

    # …and via the explicit-override path too.
    result2 = mt.resolve_judge("claude", "claude-opus-4-7", "claude", "claude-opus-4-7")
    assert result2 != ("claude", "claude-opus-4-7")
    assert result2 == ("gemini", "gemini-3.1-pro-preview")


def test_same_provider_different_explicit_models_is_allowed():
    # A stronger gemini judging a weaker gemini's output is NOT self-grade — both
    # models are explicit and differ, so the user's pick stands.
    result = mt.resolve_judge("gemini", "gemini-2.5-pro", "gemini", "gemini-3.1-pro-preview")
    assert result == ("gemini", "gemini-3.1-pro-preview")
