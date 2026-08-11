"""Wave-based batch-launch stagger — the pure offset rule.

Sizing is measured, not chosen: see the plan's Approach section
(batch d538c4ef, 2026-08-11 — per-job peak fan-out 5.54 api calls,
CREDENTIAL_MAX_CONCURRENT_GEMINI=32).
"""
import pytest

from app.services.launch_stagger import stagger_offset

WAVE = dict(wave_size=6, interval_seconds=60)


def test_first_wave_starts_immediately():
    """Jobs 0..5 fill wave 0. A launch of <= wave_size lessons must be
    byte-identical to pre-stagger behaviour: nothing is delayed."""
    assert [stagger_offset(i, **WAVE) for i in range(6)] == [0] * 6


def test_second_wave_starts_one_interval_later():
    assert [stagger_offset(i, **WAVE) for i in range(6, 12)] == [60] * 6


def test_third_wave_starts_two_intervals_later():
    assert stagger_offset(12, **WAVE) == 120
    assert stagger_offset(17, **WAVE) == 120


def test_incident_shape_28_lessons_spans_five_waves():
    """The measured incident: 28 lessons -> 5 waves, last job at +4 min."""
    offsets = [stagger_offset(i, **WAVE) for i in range(28)]
    assert offsets[0] == 0
    assert offsets[-1] == 240
    assert sorted(set(offsets)) == [0, 60, 120, 180, 240]
    # No wave may hold more than wave_size jobs, or the burst arithmetic
    # (6 x 5.54 fan-out ~= the cap of 32) stops holding.
    for off in set(offsets):
        assert offsets.count(off) <= 6


def test_offsets_never_decrease():
    """Monotonic: a later job may never become claimable before an earlier one."""
    offsets = [stagger_offset(i, **WAVE) for i in range(50)]
    assert offsets == sorted(offsets)


def test_wave_size_zero_is_the_kill_switch():
    assert stagger_offset(500, wave_size=0, interval_seconds=60) == 0


def test_interval_zero_is_the_kill_switch():
    assert stagger_offset(500, wave_size=6, interval_seconds=0) == 0


@pytest.mark.parametrize("bad", [-1, -100])
def test_negative_index_never_delays(bad):
    assert stagger_offset(bad, **WAVE) == 0
