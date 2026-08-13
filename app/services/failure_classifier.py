# app/services/failure_classifier.py
"""Deterministic classification of a phase CLI failure into a recovery class.

Pure, no I/O. `agent.run_phase_prompt` raises `RuntimeError` whose message
embeds the provider, `rc=N`, and a stderr/result snippet — we classify off
that string. Signal lists are refined against real CLI stderr during build;
anything unrecognized falls to `hard`, which the failover driver treats as
"one same-provider retry then fail over" (safe default).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class ExtractRefusal(Exception):
    """A produced extract summary failed deterministic Gate B (refusal / too
    short / junk). Classified as a wall → immediate provider failover (0
    same-provider retries), never a same-provider retry."""


# ── Transient network/transport shapes — SINGLE SOURCE OF TRUTH ──────────────
# Lower-cased substring match.  Re-exported as `agent._TRANSIENT_NET_TERMS` and
# folded into `_TRANSIENT` below, so the in-spawn retry loop
# (`agent._is_transient_net`) and the phase/queue-level retry decisions
# (`pipeline._run_with_failover`, `pipeline._requeue_worthy`) can never disagree
# about what a network blip looks like.  This module is pure (no app-level
# imports at module scope), so `agent` can import it without a cycle.
#
# They DID disagree, and it cost a lesson: on 2026-08-13 a `practice-jigsaw`
# phase died with `gemini api call failed rc=1: All connection attempts failed
# :: All connection attempts failed` — httpx's `ConnectError` text.  Both lists
# only knew requests/urllib3 and Windows-socket shapes, but `google-genai`
# speaks **httpx**, so nothing matched: `_spawn` did not back off, `classify`
# returned "hard", and `_requeue_worthy` was False → the job was marked
# `failed` at attempts=1 of QUEUE_MAX_ATTEMPTS=3 on a host that was healthy
# seconds later.
#
# Every term here must be impossible in a genuine PERMANENT failure — never
# auth (401/403/PERMISSION_DENIED/UNAUTHENTICATED), never truncation
# (MAX_TOKENS / "prompt is too long"), and never the Claude session-limit
# string "You've hit your session limit · resets …" (which must propagate
# unchanged so higher layers can auto-pause the worker).
TRANSIENT_NET_TERMS = (
    # DNS
    "getaddrinfo",
    "name resolution",
    "nameresolutionerror",
    "temporary failure in name resolution",
    # requests / urllib3
    "httpsconnectionpool",
    "max retries exceeded",
    "connection aborted",
    "connectionabortederror",
    "connection reset",
    "connectionreseterror",
    # httpx / httpcore — the stack google-genai and anthropic actually use
    "all connection attempts failed",  # httpx ConnectError (the 2026-08-13 string)
    "connecterror",                    # httpx/httpcore ConnectError repr
    "remoteprotocolerror",             # peer closed mid-response / bad framing
    "server disconnected",             # httpcore RemoteProtocolError text
    "connection refused",              # ECONNREFUSED via httpx ConnectError
    # OS / Windows socket
    "network is unreachable",
    "the network location cannot be reached",
    "semaphore timeout",
    "winerror 10053",
    "winerror 10054",
    "winerror 121",
    "winerror 1232",
    # generic (pre-existing; also covers httpx ConnectTimeout/ReadTimeout/
    # PoolTimeout, whose lower-cased class names all contain "timeout")
    "timed out",
    "timeout",
)

# Checked FIRST. NOTE: 'not your usage limit' must be matched here before the
# 'usage limit' wall substring below, or a transient server-shed is miscaught.
_TRANSIENT = (
    "not your usage limit",
    "temporarily limiting requests",
    "socket connection closed unexpectedly",
    "overloaded",
    "502",  # Bad Gateway — transient upstream/proxy blip (same family as 503)
    "503",
    "504",  # Gateway Timeout — transient upstream, retryable like 502/503
    "try again",
) + TRANSIENT_NET_TERMS
_WALL = (
    "weekly limit",
    "usage limit reached",
    "usage limit",
    "quota",
    "rate limit reached",
)
# Permanent "this model/endpoint does not exist" errors (phantom manifest entry
# or typo). The SAME bad model is requested on every same-provider retry, so a
# retry can never succeed — treat as a wall (0 same-provider retries → immediate
# failover to the next provider) instead of wasting the "hard" retry.
_MODEL_NOT_FOUND = (
    "modelnotfounderror",
    "requested entity was not found",
    "model not found",
)


# ── Session-limit detection + reset-time parser ───────────────────────────────
# Matches:  resets 12:50am (America/Chicago)
#           resets 1am
#           resets 12:50 am (America/Chicago)
#           resets 1pm
# Groups: (hour, minutes_or_None, ampm, tz_name_or_None)
_SESSION_LIMIT_RE = re.compile(
    r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:\(([^)]+)\))?",
    re.IGNORECASE,
)


def is_session_limit(text: str) -> bool:
    """Return True when *text* contains a parseable ``resets <clock-time>`` clause.

    Keys on the regex presence only — no ``now`` parameter.  The detection
    intentionally does NOT match on the phrase "session limit" because the real
    Claude CLI also emits "usage limit reached · resets 1am" (same situation,
    different wording).  The shared regex is the authoritative signal.

    Called by Task 4's failover handler BEFORE ``classify()`` so that a
    "usage limit reached" message with a clock reset is handled as a
    timed-pause (session-limit path) rather than a wall (provider-skip path).
    """
    return bool(_SESSION_LIMIT_RE.search(text))


def parse_session_limit_reset(text: str, *, now: datetime) -> datetime | None:
    """Extract the ``resets <time>`` clause and return the next future reset as
    a tz-aware UTC datetime.

    Args:
        text: Error message that may contain a ``resets …`` clause.
        now:  Injected current time (must be tz-aware).  **Never** call
              ``datetime.now()`` here — clock-discipline + testability.

    Returns:
        The next future occurrence of the reset time in UTC, or ``None`` when
        no parseable ``resets <clock-time>`` clause is found in *text*.

    Algorithm:
        1. Regex-extract hour, optional minutes, am/pm, optional IANA tz name.
        2. Default tz = ``settings.session_limit_default_tz`` when absent.
        3. Convert to 24-hour (12am→0, 12pm→12, Npm→N+12).
        4. Express ``now`` in the target tz to get "today" as a date.
        5. Build a candidate datetime for today in that tz.
        6. If candidate <= now_in_tz, roll forward one day.
        7. Convert to UTC and return.
    """
    m = _SESSION_LIMIT_RE.search(text)
    if not m:
        return None

    # Lazy import to keep the module importable without a DB/env at test time.
    from app.config import settings  # noqa: PLC0415

    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3).lower()
    tz_name = m.group(4) if m.group(4) else settings.session_limit_default_tz

    # 12-hour → 24-hour conversion
    if ampm == "am":
        if hour == 12:
            hour = 0        # 12am = midnight
    else:  # pm
        if hour != 12:
            hour += 12      # 1pm→13 … 11pm→23; 12pm stays 12

    tz = ZoneInfo(tz_name)

    # Express *now* in the target tz to determine "today" in that tz.
    now_local = now.astimezone(tz)
    today = now_local.date()

    # Build the candidate for today.
    candidate = datetime(today.year, today.month, today.day, hour, minute, 0, tzinfo=tz)

    # If the time has already passed (or is exactly now), roll to tomorrow.
    if candidate <= now_local:
        candidate = candidate + timedelta(days=1)

    return candidate.astimezone(timezone.utc)


def classify(error: "str | BaseException") -> str:
    """-> 'transient' | 'wall' | 'hard'. Transient is checked before wall."""
    if isinstance(error, ExtractRefusal):
        return "wall"
    msg = str(error).lower()
    # Permanent model-not-found wins over everything (no coincidental transient
    # match should grant a doomed same-provider retry).
    if any(s in msg for s in _MODEL_NOT_FOUND):
        return "wall"
    if any(s in msg for s in _TRANSIENT):
        return "transient"
    if any(s in msg for s in _WALL):
        return "wall"
    return "hard"
