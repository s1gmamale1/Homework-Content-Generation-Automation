"""app/services/errors.py — shared pipeline exception types.

Kept import-free (no app.* imports) so both pipeline.py and worker.py can
import from here without risk of circular dependencies.
"""

from __future__ import annotations

from datetime import datetime


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
