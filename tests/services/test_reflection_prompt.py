import pathlib

_REFLECTION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "prompts" / "_general" / "reflection.md"
)


def test_reflection_prompt_conforms_to_spec():
    text = _REFLECTION.read_text(encoding="utf-8")
    low = text.lower()
    # Stale / contradictory content must be gone:
    assert "spaced repetition" not in low, "superseded Consolidation block still present"
    assert "easy or hard" not in low, "dead easy/hard mode reference still present"
    assert "kuchaytirad\n" not in text and "kuchaytirad." not in text, "typo not fixed"
    assert "≥60%" not in text and "<60%" not in text, "score-branch contradiction still present"
    # Debrief STRUCTURE must be present (content, not a live score):
    assert "{{SUBJECT}}" in text and "{{LANGUAGE_RULES}}" in text
    for marker in ("redo", "kuchli", "zaif"):   # redo route + strong/weak-point prompts (UZ: kuchli/zaif)
        assert marker in low, f"missing debrief element: {marker}"


def test_reflection_does_not_pre_assert_attempt_outcomes():
    text = _REFLECTION.read_text(encoding="utf-8")
    low = text.lower()
    # The app owns pass/fail; the prompt must NOT name a not-passed outcome, and
    # must NOT ask the model to report how the student's answers performed
    # (there is no attempt at generation time — that produced the audit's
    # fabricated "Needs Retry / ikkilanishlar kuzatildi" narratives).
    assert "needs retry" not in low, "reflection still names a not-passed outcome"
    assert "homework not passed" not in low, "reflection still pre-asserts a fail outcome"
    assert "handled well" not in low, "reflection still asks to report the student's performance"
    # Structure must remain (app fills it after the real attempt):
    assert "kuchli" in low and "zaif" in low and "redo" in low
    assert "app" in low  # states the app owns pass/redo
