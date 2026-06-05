# app/services/failure_classifier.py
"""Deterministic classification of a phase CLI failure into a recovery class.

Pure, no I/O. `agent.run_phase_prompt` raises `RuntimeError` whose message
embeds the provider, `rc=N`, and a stderr/result snippet — we classify off
that string. Signal lists are refined against real CLI stderr during build;
anything unrecognized falls to `hard`, which the failover driver treats as
"one same-provider retry then fail over" (safe default).
"""

from __future__ import annotations


class ExtractRefusal(Exception):
    """A produced extract summary failed deterministic Gate B (refusal / too
    short / junk). Classified as a wall → immediate provider failover (0
    same-provider retries), never a same-provider retry."""


# Checked FIRST. NOTE: 'not your usage limit' must be matched here before the
# 'usage limit' wall substring below, or a transient server-shed is miscaught.
_TRANSIENT = (
    "not your usage limit",
    "temporarily limiting requests",
    "socket connection closed unexpectedly",
    "connection reset",
    "timed out",
    "timeout",
    "overloaded",
    "503",
    "try again",
)
_WALL = (
    "weekly limit",
    "usage limit reached",
    "usage limit",
    "quota",
    "rate limit reached",
)


def classify(error: "str | BaseException") -> str:
    """-> 'transient' | 'wall' | 'hard'. Transient is checked before wall."""
    if isinstance(error, ExtractRefusal):
        return "wall"
    msg = str(error).lower()
    if any(s in msg for s in _TRANSIENT):
        return "transient"
    if any(s in msg for s in _WALL):
        return "wall"
    return "hard"
