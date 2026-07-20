"""Typed queue-correctness signals (errors.py).

RED-proofs: is_slot_saturation must match the exact _spawn_once marker and
reject near-misses; PhaseAttemptTimeout must never stringify blank (the
asyncio.TimeoutError bug this replaces)."""
from app.services.errors import (
    SLOT_SATURATION_MARKER,
    PhaseAttemptTimeout,
    SlotSaturation,
    TransientPhaseError,
    is_slot_saturation,
)


def test_marker_matches_spawn_once_literal():
    # Must equal the literal in agent._spawn_once's slot-exhaustion return.
    assert SLOT_SATURATION_MARKER == "fleet credential slot wait exhausted"


def test_is_slot_saturation_on_exception_and_string():
    exc = RuntimeError(
        "gemini api call failed rc=1: 429 fleet credential slot wait "
        "exhausted (credential=gemini:project-x, budget=120s)"
    )
    assert is_slot_saturation(exc) is True
    assert is_slot_saturation(str(exc)) is True


def test_is_slot_saturation_rejects_plain_429():
    assert is_slot_saturation(RuntimeError("429 RESOURCE_EXHAUSTED")) is False


def test_phase_attempt_timeout_never_blank():
    exc = PhaseAttemptTimeout("per-attempt timeout after 600s (provider=gemini)")
    assert str(exc)  # non-empty — the whole point
    assert "600" in str(exc)


def test_signal_types_are_distinct():
    # Worker/pipeline dispatch on type — none may inherit from another.
    for a, b in [(SlotSaturation, TransientPhaseError),
                 (SlotSaturation, PhaseAttemptTimeout),
                 (TransientPhaseError, PhaseAttemptTimeout)]:
        assert not issubclass(a, b) and not issubclass(b, a)
