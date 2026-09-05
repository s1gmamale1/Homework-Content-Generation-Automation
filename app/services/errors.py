"""app/services/errors.py — shared pipeline exception types.

Kept import-free (no app.* imports) so both pipeline.py and worker.py can
import from here without risk of circular dependencies.
"""

from __future__ import annotations

from datetime import datetime


class AuthEnvError(RuntimeError):
    """The requested API transport lacks its provider credential."""


class BookFetchError(RuntimeError):
    """A worker could not obtain a book's ``source.pdf`` from the head (R13).

    RuntimeError-derived on purpose: the read-site has always raised
    ``RuntimeError`` and callers/tests match on that, so this narrows the type
    without breaking anyone.
    """


class BookFetchTimeout(BookFetchError):
    """The ``source.pdf`` fetch blew its own budget
    (``settings.book_fetch_timeout_seconds``) — waiting behind another job's
    in-flight fetch of the same book, or trickling bytes off the head.

    Distinct from the job timeout by design: before this existed, a slow fetch
    silently consumed the entire ``job_timeout_seconds`` and surfaced as a bare
    ``timeout after 1800s`` with ``current_phase=NULL``, which names neither the
    fetch nor the book that caused it.
    """


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
    if isinstance(exc, (PersistentContentQualityFailure, PersistentSolverMismatch)):
        return False  # quoted exercise/reviewer text is not a provider signal
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


class PersistentSolverMismatch(Exception):
    """A solver-confirmed answer-key defect survived the bounded regen.

    This is a hard content-quality failure, not a provider transient. The
    phase and job must fail and must never be archived/distributed.
    """

    _MAX_MESSAGE_CHARS = 900

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        value = value.strip()
        if len(value) <= limit:
            return value
        return f"{value[: limit - 3].rstrip()}..."

    def __init__(
        self,
        phase_name: str,
        warnings: list[str],
        repair_error: BaseException | None = None,
    ) -> None:
        self.phase_name = phase_name
        self.warnings = tuple(warnings)
        self.repair_error = repair_error
        phase_context = self._clip(phase_name or "unknown phase", 80)
        warning_text = "; ".join(self.warnings[:3]) or "solver supplied no detail"
        shown = self._clip(warning_text, 400)
        suffix = ""
        if repair_error is not None:
            cause_type = self._clip(type(repair_error).__name__, 80)
            cause_text = str(repair_error).strip() or repr(repair_error)
            suffix = f"; repair failed ({cause_type}): {self._clip(cause_text, 220)}"
        message = (
            f"{phase_context}: persistent answer-key mismatch after regeneration: "
            f"{shown}{suffix}"
        )
        super().__init__(self._clip(message, self._MAX_MESSAGE_CHARS))


class PersistentContentQualityFailure(Exception):
    """A known major learner defect remains unresolved after bounded repair.

    Carries the full warnings for inspection; the exception message is bounded.
    This type is terminal even when quoted content contains provider error words.
    """

    def __init__(self, phase_name: str, warnings: list[str],
                 repair_error: BaseException | None = None) -> None:
        self.phase_name = phase_name
        self.warnings = tuple(warnings)
        self.repair_error = repair_error
        clip = PersistentSolverMismatch._clip
        detail = clip("; ".join(warnings[:3]) or "major defect unresolved", 400)
        suffix = ""
        if repair_error is not None:
            suffix = f"; repair failed ({type(repair_error).__name__}): {clip(str(repair_error), 220)}"
        super().__init__(clip(
            f"{clip(phase_name, 80)}: persistent content-quality failure: {detail}{suffix}", 900,
        ))


# ── Fenced-lease control signals (fenced job leases, Task 7) ─────────────────
#
# These are CONTROL SIGNALS, not content errors. A fenced worker write
# (jobs_repo / phase_repo with a claim_token) returns a ``lease.LeaseLost`` /
# ``lease.CancelRequested`` sentinel; pipeline converts that sentinel into one
# of these signals and lets it unwind ``pipeline.run`` WITHOUT being turned into
# a TransientPhaseError, a content-error job-failure, a judge failure, or a queue
# retry. Every broad ``except (SessionLimitPause, SlotSaturation,
# TransientPhaseError)`` / bare ``except Exception`` boundary in the pipeline
# re-raises them (like the code already re-raises around CancelledError), so no
# handler can swallow them. The worker (``_execute_job``) is the only place that
# acts on them: LeaseLost → cancel this execution's local task, mutate nothing;
# CancelWon → the repo ALREADY finalized cancelled (single-finalize contract),
# so just stop — never finalize again.


class LeaseLostSignal(Exception):
    """A fenced worker write found the lease no longer owns the job — a reclaim
    rotated the claim_token to a new owner. Unwinds pipeline.run; the worker
    cancels this execution's local task and mutates NOTHING (the job now belongs
    to the reclaiming worker)."""


class CancelWonSignal(Exception):
    """A fenced worker write found a user cancel won and the repo has ALREADY
    finalized the job (cancelled + phase sweep + released_cancelled — the
    single-finalize contract). Unwinds pipeline.run; the worker cancels its
    local task and does NOT finalize again."""
