import enum
import uuid
from dataclasses import dataclass

from app.models.homework_job import HomeworkJob


@dataclass(frozen=True)
class JobLease:
    job_id: uuid.UUID
    claim_token: uuid.UUID
    owner_id: str


@dataclass(frozen=True)
class ClaimedJob:
    job: HomeworkJob
    lease: JobLease


class _Sentinel:
    __slots__ = ("_name",)

    def __init__(self, name):
        self._name = name

    def __repr__(self):
        return f"<{self._name}>"


LeaseLost = _Sentinel("LeaseLost")
CancelRequested = _Sentinel("CancelRequested")


class HeartbeatOutcome(enum.Enum):
    RENEWED = "renewed"
    CANCELLING = "cancelling"
    LOST = "lost"


EVENT_CLAIMED = "claimed"
EVENT_RECLAIMED_STALE = "reclaimed_stale"
EVENT_RECLAIMED_FORCED = "reclaimed_forced"
EVENT_RELEASED_DONE = "released_done"
EVENT_RELEASED_RETRY = "released_retry"
EVENT_RELEASED_FAILED = "released_failed"
EVENT_RELEASED_CANCELLED = "released_cancelled"
EVENT_LEASE_LOST = "lease_lost"
