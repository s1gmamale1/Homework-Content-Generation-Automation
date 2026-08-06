import uuid, dataclasses, pytest
from app.services import lease

def test_joblease_is_frozen():
    l = lease.JobLease(job_id=uuid.uuid4(), claim_token=uuid.uuid4(), owner_id="h:1@sha")
    with pytest.raises(dataclasses.FrozenInstanceError):
        l.claim_token = uuid.uuid4()

def test_sentinels_are_distinct_singletons():
    assert lease.LeaseLost is lease.LeaseLost
    assert lease.LeaseLost is not lease.CancelRequested

def test_heartbeat_outcomes_exist():
    assert {lease.HeartbeatOutcome.RENEWED, lease.HeartbeatOutcome.CANCELLING,
            lease.HeartbeatOutcome.LOST}

def test_event_constants():
    assert lease.EVENT_CLAIMED == "claimed"
    assert lease.EVENT_RECLAIMED_FORCED == "reclaimed_forced"
