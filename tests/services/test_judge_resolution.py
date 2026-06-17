from app.services import model_tiers as mt

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
    # is the safe auto-tier judge (guaranteed non-self), NOT _SELF_FALLBACK.
    result = mt.resolve_judge(_GEN_PROV, _GEN_MODEL, _GEN_PROV, _GEN_MODEL)
    assert result != (_GEN_PROV, _GEN_MODEL)                      # never the generator
    assert result == mt.judge_model_for(_GEN_PROV, _GEN_MODEL)


def test_null_judge_model_same_provider_is_not_self_grade():
    # REGRESSION (the reviewer's reachable bug): a gemini generator (explicit) + a
    # gemini judge with Auto/None model must NOT self-grade. None must resolve to
    # the provider default BEFORE the equality check, else ("gemini", None) !=
    # ("gemini", "gemini-3.1-pro-preview") slipped past and the judge spawned
    # gemini's default == the generator.
    result = mt.resolve_judge("gemini", "gemini-3.1-pro-preview", "gemini", None)
    assert result != ("gemini", "gemini-3.1-pro-preview")        # not the generator
    assert result != ("gemini", None)                            # not an ambiguous self
    assert result == mt.judge_model_for("gemini", "gemini-3.1-pro-preview")


def test_both_auto_same_provider_is_not_self_grade():
    # generator on gemini default + judge on gemini default -> still independent.
    result = mt.resolve_judge("gemini", None, "gemini", None)
    assert result != ("gemini", None)
    assert result == mt.judge_model_for("gemini", None)


def test_self_grade_fallback_is_never_self_for_a_gemini_3_1_generator():
    # The specific case a raw _SELF_FALLBACK swap would have broken: a
    # gemini-3.1-pro generator. The result must NOT be gemini-3.1-pro-preview
    # (which _SELF_FALLBACK happens to be).
    result = mt.resolve_judge(
        "gemini", "gemini-3.1-pro-preview", "gemini", "gemini-3.1-pro-preview")
    assert result != ("gemini", "gemini-3.1-pro-preview")
    assert result != mt._SELF_FALLBACK  # _SELF_FALLBACK IS gemini-3.1-pro-preview


def test_same_provider_different_explicit_models_is_allowed():
    # A stronger gemini judging a weaker gemini's output is NOT self-grade — both
    # models are explicit and differ, so the user's pick stands.
    result = mt.resolve_judge("gemini", "gemini-2.5-pro", "gemini", "gemini-3.1-pro-preview")
    assert result == ("gemini", "gemini-3.1-pro-preview")
