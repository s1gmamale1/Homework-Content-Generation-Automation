"""Tests for the _scheduler_stuck_message helper in pipeline.py (flow-1 fix).

These are pure unit tests — no DB, no asyncio, no subprocess.
"""

from app.services.pipeline import _scheduler_stuck_message


def test_scheduler_stuck_message_interpolates_real_deps():
    """The message must contain the *actual* resolved dep list, not the
    literal comprehension source text."""
    # Use a real phase that has known deps: "memory-check" depends on
    # ["flashcards"] per PHASE_DEPS.  Provide a content_phases list that
    # includes "flashcards" so resolve_phase_deps returns {"flashcards"}.
    content_phases = ["case-based-preview", "flashcards", "memory-check"]
    pending = {"memory-check"}

    msg = _scheduler_stuck_message(pending, content_phases)

    # (a) The actual dep value must appear in the message.
    assert "flashcards" in msg, f"Expected 'flashcards' in message, got: {msg!r}"

    # (b) The comprehension must NOT appear as literal source text.
    assert "for p in" not in msg, (
        f"Message still contains the literal comprehension 'for p in', got: {msg!r}"
    )


def test_scheduler_stuck_message_contains_pending_phase():
    """The pending phase name itself must appear in the message."""
    content_phases = ["case-based-preview", "flashcards", "memory-check"]
    pending = {"memory-check"}

    msg = _scheduler_stuck_message(pending, content_phases)

    assert "memory-check" in msg, f"Expected 'memory-check' in message, got: {msg!r}"


def test_scheduler_stuck_message_no_deps_phase():
    """A phase with no declared deps (e.g. 'case-based-preview') should
    show an empty dep set — still no literal comprehension text."""
    content_phases = ["case-based-preview", "flashcards"]
    pending = {"case-based-preview"}

    msg = _scheduler_stuck_message(pending, content_phases)

    assert "for p in" not in msg, (
        f"Message still contains the literal comprehension text: {msg!r}"
    )
    assert "case-based-preview" in msg
