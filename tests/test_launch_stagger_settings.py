"""Launch-stagger knobs + the Semaphore(0) silent-brick guard."""
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_the_measured_incident():
    """6 x 5.54 measured fan-out ~= 33 calls vs a cap of 32; 60s clears the
    16.1s max extract plus one ~36s content call."""
    s = Settings(_env_file=None)
    assert s.batch_launch_wave_size == 6
    assert s.batch_launch_wave_interval_seconds == 60


def test_knobs_are_overridable():
    s = Settings(_env_file=None, batch_launch_wave_size=4,
                 batch_launch_wave_interval_seconds=90)
    assert s.batch_launch_wave_size == 4
    assert s.batch_launch_wave_interval_seconds == 90


@pytest.mark.parametrize("kwargs", [
    {"batch_launch_wave_size": 0},
    {"batch_launch_wave_interval_seconds": 0},
])
def test_zero_is_an_allowed_kill_switch(kwargs):
    """0 must be ACCEPTED here — it is the documented way to turn the stagger
    off without a deploy."""
    assert Settings(_env_file=None, **kwargs) is not None


@pytest.mark.parametrize("kwargs", [
    {"batch_launch_wave_size": -1},
    {"batch_launch_wave_interval_seconds": -1},
])
def test_negative_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("field", ["agent_max_concurrency", "gemini_max_concurrency"])
def test_zero_concurrency_is_rejected_not_silently_deadlocking(field):
    """asyncio.Semaphore(0) blocks FOREVER: a worker would claim jobs, make no
    model call, log nothing, and lose every job to the job timeout. Fail at
    startup instead of bricking silently."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})
