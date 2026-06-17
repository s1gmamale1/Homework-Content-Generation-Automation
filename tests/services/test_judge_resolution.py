from app.services import model_tiers as mt

# Real ids from _MODEL_TIER:
#   tier 1 (strongest): claude/claude-opus-4-7, gemini/gemini-3.1-pro-preview
#   tier 2 (weaker):    gemini/gemini-2.5-pro, claude/claude-sonnet-4-6
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


def test_exact_self_grade_is_hard_swapped():
    # explicit judge == generator -> never self-grade
    assert mt.resolve_judge(_GEN_PROV, _GEN_MODEL, _GEN_PROV, _GEN_MODEL) == mt._SELF_FALLBACK


def test_advisory_warns_when_judge_weaker():
    # tier-1 generator graded by a tier-2 judge -> advisory string (not None)
    assert (
        mt.judge_advisory("claude", "claude-opus-4-7", "gemini", "gemini-2.5-pro")
        is not None
    )
    # stronger-or-equal judge -> None
    assert (
        mt.judge_advisory("gemini", "gemini-2.5-pro", "claude", "claude-opus-4-7")
        is None
    )
