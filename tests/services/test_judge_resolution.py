"""Tests for model_tiers.resolve_judge.

``resolve_judge`` is the single call-site that integrates the explicit-override
branch and the self-grade-swap branch.  ``judge_model_for`` is now deterministic
(no settings read) so these tests no longer need monkeypatch — the assertions
follow from the hardcoded frontier-peer constants alone.

Real ids from _MODEL_TIER:
  tier 1 (strongest): claude/claude-opus-4-7, gemini/gemini-3.1-pro-preview
  tier 2 (weaker):    gemini/gemini-2.5-pro, claude/claude-sonnet-4-6
gemini's default model (agent_models.default_model) is gemini-3.1-pro-preview.
"""
from app.services import model_tiers as mt

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


def test_null_judge_model_same_provider_is_not_self_grade():
    # REGRESSION: a gemini generator (explicit) + a gemini judge with Auto/None
    # model must NOT self-grade. None resolves to the provider default BEFORE the
    # equality check. Now deterministic (no settings read needed).
    # gemini default = gemini-3.1-pro-preview = same as the generator here.
    result = mt.resolve_judge("gemini", "gemini-3.1-pro-preview", "gemini", None)
    assert result != ("gemini", "gemini-3.1-pro-preview")        # not the generator
    assert result != ("gemini", None)                            # not an ambiguous self
    assert result == ("claude", "claude-opus-4-7")               # the non-gemini peer


def test_self_fallback_holds_when_generator_is_gemini_3_1():
    # Even when the generator IS gemini-3.1-pro-preview (the alt fallback model),
    # judge_model_for must return the claude primary peer — not the generator.
    # This is now purely deterministic (no settings influence).
    result = mt.judge_model_for("gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_both_auto_same_provider_is_not_self_grade():
    # generator on gemini default + judge on gemini default -> still independent.
    result = mt.resolve_judge("gemini", None, "gemini", None)
    assert result != ("gemini", None)
    assert result == mt.judge_model_for("gemini", None)


def test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator():
    # resolve_judge with an explicit judge == the gemini-3.1 generator must swap to
    # the non-gemini peer — deterministic, no settings needed.
    result = mt.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result == ("claude", "claude-opus-4-7")


def test_self_fallback_is_non_self_for_a_claude_opus_generator():
    # THE OPTION-B FIX: a claude-opus-4-7 generator with explicit judge also
    # claude-opus-4-7 must NOT self-grade. The generator-aware fallback returns
    # the distinct alternate peer. Now purely deterministic.
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
