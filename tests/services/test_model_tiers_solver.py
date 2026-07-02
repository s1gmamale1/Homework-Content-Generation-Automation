"""Unit tests for ``app.services.model_tiers.resolve_solver``.

Coverage:
- Explicit override honored when it is not a self-grade.
- Self-grade (solver == generator) swapped to a frontier peer.
- Null override resolves a non-self frontier peer.

``resolve_solver`` has the identical self-grade-guard policy as
``resolve_judge`` — a solver, like a judge, must never solve/grade against
the generator's own model.
"""

from __future__ import annotations

from app.services import model_tiers as mt


def test_explicit_override_is_honored_when_not_self():
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", "claude", "claude-opus-4-7")
    assert (p, m) == ("claude", "claude-opus-4-7")


def test_self_grade_is_swapped_to_a_frontier_peer():
    # solver override == generator → must NOT be allowed to grade itself
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", "gemini", "gemini-2.5-flash")
    assert (p, m) != ("gemini", "gemini-2.5-flash")


def test_null_override_resolves_a_non_self_frontier_peer():
    p, m = mt.resolve_solver("gemini", "gemini-2.5-flash", None, None)
    assert p and (p, m) != ("gemini", "gemini-2.5-flash")
