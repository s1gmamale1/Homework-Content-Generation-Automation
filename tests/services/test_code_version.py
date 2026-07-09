"""Unit tests for app/services/code_version.py (fleet-worker-version-gate-1).

detect() is tested with subprocess mocked (deterministic) PLUS one real-git
integration test against this repo itself (git is guaranteed present in dev/CI
checkouts). is_stale() is a pure truth table.

RED-proofs:
  - is_stale: without the floor-None short-circuit, (None, None) would compare and crash.
  - is_stale: without the version-None branch, an undetectable worker would PASS the gate.
  - detect env override: without the override branch, WORKER_CODE_VERSION would be ignored.
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import code_version


# ─── is_stale truth table ───────────────────────────────────────────────

def test_is_stale_no_floor_never_stale():
    assert code_version.is_stale(5, None) is False
    assert code_version.is_stale(None, None) is False


def test_is_stale_unknown_version_with_floor_is_stale():
    assert code_version.is_stale(None, 100) is True


def test_is_stale_below_floor():
    assert code_version.is_stale(99, 100) is True


def test_is_stale_at_or_above_floor():
    assert code_version.is_stale(100, 100) is False
    assert code_version.is_stale(101, 100) is False


# ─── detect() ───────────────────────────────────────────────────────────

def test_detect_env_override_wins_for_number():
    with patch.object(code_version, "_git", return_value="abc1234"):
        version, sha = code_version.detect({"WORKER_CODE_VERSION": "777"})
    assert version == 777
    assert sha == "abc1234"


def test_detect_env_override_non_integer_falls_through_to_git():
    def fake_git(*args):
        return "abc1234" if args[0] == "rev-parse" else "1234"
    with patch.object(code_version, "_git", side_effect=fake_git):
        version, sha = code_version.detect({"WORKER_CODE_VERSION": "not-a-number"})
    assert version == 1234
    assert sha == "abc1234"


def test_detect_git_unavailable_returns_none_pair():
    with patch.object(code_version, "_git", return_value=None):
        version, sha = code_version.detect({})
    assert version is None
    assert sha is None


def test_detect_shallow_clone_refuses_bogus_count():
    """A shallow clone's rev-list count is the truncated depth, not the true
    count — detect() must return None (fail-closed, loud) instead of reporting
    an ancient-looking version that no pull would ever fix.

    RED-proof: without the is-shallow check, this returns (1, sha)."""
    def fake_git(*args):
        if args[0] == "rev-parse" and args[1] == "--short":
            return "abc1234"
        if args == ("rev-parse", "--is-shallow-repository"):
            return "true"
        if args[0] == "rev-list":
            return "1"  # the bogus truncated depth
        return None
    with patch.object(code_version, "_git", side_effect=fake_git):
        version, sha = code_version.detect({})
    assert version is None
    assert sha == "abc1234"


def test_detect_real_git_in_this_repo():
    """Integration: this test runs inside the repo checkout (full clone or
    linked worktree), so real git must yield a positive count and a hex short
    sha. REQUIRES A FULL CLONE — a shallow CI checkout would (correctly)
    yield version=None and fail this test's environment assumption."""
    version, sha = code_version.detect({})
    assert isinstance(version, int) and version > 100
    assert isinstance(sha, str) and 6 <= len(sha) <= 12
    int(sha, 16)  # raises if not hex


def test_module_globals_computed_at_import():
    assert code_version.CODE_VERSION is None or isinstance(code_version.CODE_VERSION, int)
    # In this repo checkout they must actually be populated:
    assert code_version.CODE_VERSION is not None
    assert code_version.GIT_SHA is not None
