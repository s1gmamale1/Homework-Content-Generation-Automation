"""Tests for the max_judge_regens setting (judge-quality Cluster 3)."""

import pytest
from app.config import Settings


def test_max_judge_regens_default_is_one():
    """max_judge_regens should default to 1 (current single-regen behavior)."""
    s = Settings(_env_file=None)
    assert s.max_judge_regens == 1


def test_max_judge_regens_env_override():
    """MAX_JUDGE_REGENS env var should override the default."""
    s = Settings(_env_file=None, max_judge_regens=3)
    assert s.max_judge_regens == 3


def test_max_judge_regens_zero_allowed():
    """max_judge_regens=0 should be allowed (disables regeneration)."""
    s = Settings(_env_file=None, max_judge_regens=0)
    assert s.max_judge_regens == 0
