# tests/services/test_session_limit_classify_parse.py
"""TDD tests for is_session_limit + parse_session_limit_reset.

Fixtures are sourced from the real log strings captured 2026-06-23
(var/server oliver worker.log):
  You've hit your session limit · resets 12:50am (America/Chicago)

All `now` values are fixed tz-aware datetimes — no datetime.now() here.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from app.services.failure_classifier import is_session_limit, parse_session_limit_reset


# ── Verbatim real strings from the Oliver-worker log ──────────────────────────

REAL_MSG = "You've hit your session limit · resets 12:50am (America/Chicago)"
REAL_FULL = (
    "phase.run practice-sentence: claude CLI exited rc=1 :: "
    "You've hit your session limit · resets 12:50am (America/Chicago)"
)
USAGE_VARIANT = "usage limit reached · resets 1am"
NO_CLOCK_WALL = "You have reached your weekly limit reached"
PROMPT_TOO_LONG = "Prompt is too long"


# ── is_session_limit ───────────────────────────────────────────────────────────

def test_real_string_is_session_limit():
    assert is_session_limit(REAL_MSG) is True


def test_full_error_string_is_session_limit():
    """Wrapped in the full RuntimeError message shape from pipeline logs."""
    assert is_session_limit(REAL_FULL) is True


def test_usage_limit_reached_with_reset_clock_is_session_limit():
    """'usage limit reached · resets 1am' must ALSO fire — the detection key is
    the parseable resets <clock-time> clause, not the 'session limit' phrase.
    This proves Task 4's before-classify check will catch this phrasing too."""
    assert is_session_limit(USAGE_VARIANT) is True


def test_no_clock_reset_wall_is_not_session_limit():
    """A weekly/quota wall with no clock reset time → False."""
    assert is_session_limit(NO_CLOCK_WALL) is False


def test_prompt_too_long_is_not_session_limit():
    assert is_session_limit(PROMPT_TOO_LONG) is False


def test_empty_string_is_not_session_limit():
    assert is_session_limit("") is False


# ── parse_session_limit_reset — reset still in the future today ───────────────

def test_parse_real_string_reset_later_today():
    """12:50am Chicago; now is 11pm the same day (Chicago) → reset is 12:50am
    the SAME night-crossing: 11pm is before midnight, 12:50am is past midnight.
    Wait — 12:50am has already passed by 11pm (11pm is after 12:50am the same
    calendar night). Let's pick now = 11:00pm previous evening (still before
    12:50am of the upcoming midnight). Actually, 12:50am is 00:50. If now is
    22:00 (10pm), then 00:50 tomorrow is the next future occurrence.

    Let's be explicit: Chicago midnight = 00:00. 12:50am = 00:50.
    now = 2026-06-23 22:00:00 Chicago (10pm) → 00:50 is still in the future
    (2026-06-24 00:50 Chicago = 2026-06-24 05:50 UTC).
    """
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    # now = 2026-06-23 22:00 Chicago = 2026-06-24 03:00 UTC (CDT = UTC-5)
    now = datetime(2026, 6, 23, 22, 0, 0, tzinfo=chicago)
    result = parse_session_limit_reset(REAL_MSG, now=now)
    assert result is not None
    # Expected: 2026-06-24 00:50 Chicago (CDT = UTC-5 = UTC+(-5))
    # 00:50 CDT = 05:50 UTC
    expected = datetime(2026, 6, 24, 5, 50, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_real_string_reset_already_past_today_rolls_to_tomorrow():
    """12:50am Chicago; now is 01:00am Chicago (already past 12:50am).
    → must roll to tomorrow: 2026-06-24 00:50 Chicago.

    now = 2026-06-23 01:00 Chicago (after 12:50am → time has passed today)
    next future = 2026-06-24 00:50 Chicago = 2026-06-24 05:50 UTC.
    """
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 1, 0, 0, tzinfo=chicago)
    result = parse_session_limit_reset(REAL_MSG, now=now)
    assert result is not None
    # past 12:50am → roll to tomorrow: 2026-06-24 00:50 Chicago = 05:50 UTC
    expected = datetime(2026, 6, 24, 5, 50, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_reset_exactly_at_reset_time_rolls_to_tomorrow():
    """now == reset time exactly → roll to tomorrow (not-yet-future)."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 0, 50, 0, tzinfo=chicago)
    result = parse_session_limit_reset(REAL_MSG, now=now)
    assert result is not None
    expected = datetime(2026, 6, 24, 5, 50, 0, tzinfo=timezone.utc)
    assert result == expected


# ── parse_session_limit_reset — no-minutes variant ────────────────────────────

def test_parse_usage_variant_no_minutes_no_tz():
    """'resets 1am' — no minutes, no tz → defaults to settings.session_limit_default_tz.

    Default tz is America/Chicago. 1am = 01:00.
    now = 2026-06-23 00:00 Chicago (midnight — 1am not yet reached).
    Expected: 2026-06-23 01:00 Chicago = 06:00 UTC (CDT = UTC-5).
    """
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 0, 0, 0, tzinfo=chicago)
    result = parse_session_limit_reset(USAGE_VARIANT, now=now)
    assert result is not None
    expected = datetime(2026, 6, 23, 6, 0, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_1pm_no_tz():
    """'resets 1pm' (13:00) with now at noon → still today."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 12, 0, 0, tzinfo=chicago)
    msg = "You hit a limit · resets 1pm"
    result = parse_session_limit_reset(msg, now=now)
    assert result is not None
    # 1pm = 13:00 Chicago CDT = 18:00 UTC
    expected = datetime(2026, 6, 23, 18, 0, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_1pm_past_rolls_tomorrow():
    """'resets 1pm' with now at 2pm → roll to tomorrow."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 14, 0, 0, tzinfo=chicago)
    msg = "You hit a limit · resets 1pm"
    result = parse_session_limit_reset(msg, now=now)
    assert result is not None
    # 1pm tomorrow Chicago CDT = 2026-06-24 18:00 UTC
    expected = datetime(2026, 6, 24, 18, 0, 0, tzinfo=timezone.utc)
    assert result == expected


# ── parse_session_limit_reset — am/pm with space ─────────────────────────────

def test_parse_space_between_time_and_ampm():
    """'resets 12:50 am (America/Chicago)' — space before am."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 22, 0, 0, tzinfo=chicago)
    msg = "session limit · resets 12:50 am (America/Chicago)"
    result = parse_session_limit_reset(msg, now=now)
    assert result is not None
    expected = datetime(2026, 6, 24, 5, 50, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_12pm_noon():
    """12pm = noon = 12:00 in 24h."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 11, 0, 0, tzinfo=chicago)
    msg = "resets 12pm"
    result = parse_session_limit_reset(msg, now=now)
    assert result is not None
    # 12pm = 12:00 Chicago CDT = 17:00 UTC
    expected = datetime(2026, 6, 23, 17, 0, 0, tzinfo=timezone.utc)
    assert result == expected


def test_parse_12am_midnight():
    """12am = midnight = 00:00 in 24h."""
    from zoneinfo import ZoneInfo
    chicago = ZoneInfo("America/Chicago")
    now = datetime(2026, 6, 23, 23, 0, 0, tzinfo=chicago)
    msg = "resets 12am"
    result = parse_session_limit_reset(msg, now=now)
    assert result is not None
    # midnight tomorrow Chicago CDT = 2026-06-24 05:00 UTC
    expected = datetime(2026, 6, 24, 5, 0, 0, tzinfo=timezone.utc)
    assert result == expected


# ── parse_session_limit_reset — no match cases ───────────────────────────────

def test_parse_no_match_returns_none():
    assert parse_session_limit_reset(PROMPT_TOO_LONG, now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)) is None


def test_parse_no_clock_wall_returns_none():
    assert parse_session_limit_reset(NO_CLOCK_WALL, now=datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)) is None


# ── now must be tz-aware (sanity: UTC now works too) ─────────────────────────

def test_parse_with_utc_now():
    """Parser accepts UTC-aware now and returns UTC result."""
    # now = 2026-06-23 03:00 UTC = 2026-06-22 22:00 CDT
    # 12:50am CDT is 05:50 UTC the same day → still future relative to 03:00 UTC
    now = datetime(2026, 6, 23, 3, 0, 0, tzinfo=timezone.utc)
    result = parse_session_limit_reset(REAL_MSG, now=now)
    assert result is not None
    # 12:50am CDT on 2026-06-23 = 05:50 UTC on 2026-06-23 → future vs 03:00 UTC
    expected = datetime(2026, 6, 23, 5, 50, 0, tzinfo=timezone.utc)
    assert result == expected
