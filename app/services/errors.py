"""app/services/errors.py — shared pipeline exception types.

Kept import-free (no app.* imports) so both pipeline.py and worker.py can
import from here without risk of circular dependencies.
"""

from __future__ import annotations

from datetime import datetime


class AuthEnvError(RuntimeError):
    """The requested API transport lacks its provider credential."""


class SessionLimitPause(Exception):
    """Raised by _run_with_failover when ``session_limit_strategy='pause'`` and
    a session-limit error is detected on the requested provider.

    Signals the worker to requeue the job after a cooldown (Task 5).  The job
    must NOT be marked ``failed`` when this propagates — it should be parked
    until ``reset_at``.

    Attributes:
        reset_at: The next future reset time in UTC, parsed from the error
                  message.  ``None`` when parsing fails (unusual clock format).
    """

    def __init__(self, reset_at: datetime | None) -> None:
        self.reset_at = reset_at
        reset_str = reset_at.isoformat() if reset_at else "unknown"
        super().__init__(f"session-limit pause — resets at {reset_str}")


# ── Queue-correctness signals (queue-correctness-1) ──────────────────────────

# Must match the literal embedded in agent._spawn_once's slot-exhaustion
# return ("429 fleet credential slot wait exhausted (credential=…, budget=…)").
SLOT_SATURATION_MARKER = "fleet credential slot wait exhausted"


def is_slot_saturation(exc: "BaseException | str") -> bool:
    """True when an error/text carries the fleet slot-exhaustion marker."""
    return SLOT_SATURATION_MARKER in str(exc)


class SlotSaturation(Exception):
    """Fleet credential-slot wait exhausted. The worker parks the job
    (status='pending', scheduled_at pushed by a cooldown, attempt refunded) —
    the job must NOT be marked failed and must NOT burn a retry attempt."""


class TransientPhaseError(Exception):
    """A phase failed with a transient-class error (attempt timeout, 429,
    net blip) after in-process retries were exhausted. Propagates to the
    worker so jobs_repo.mark_failed_with_retry applies the bounded queue
    retry. Message shape: '<phase>: <reason>'."""


class PhaseAttemptTimeout(Exception):
    """An attempt exceeded settings.per_attempt_timeout_seconds. Replaces the
    raw asyncio.TimeoutError whose str() is '' (blank error_message bug)."""
