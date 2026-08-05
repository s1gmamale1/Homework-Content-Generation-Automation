"""The cross-repo gate must be incapable of passing by skipping.

`test_platform_contract.py` skips when the platform checkout is absent — right
on a laptop, catastrophic in CI, where a skipped cross-repo gate reports green
while checking nothing. That is not hypothetical: the gate's *absence* is what
let a `payload`-less envelope, a string `grade` and a silently-dropped phase
reach a PR.

So `REQUIRE_PLATFORM_CONTRACT=1` converts every skip in that file into a hard
failure, and these tests hold that conversion in place. They drive the real
mechanism — a subprocess pytest with a deliberately absent `PLATFORM_SRC` —
rather than asserting on the helper, because the property under test is the
*exit code an operator sees*, not an internal branch.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_TARGET = "tests/conformance/test_platform_contract.py"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ABSENT = "/nonexistent/platform-checkout-for-this-test"


def _run(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, "PLATFORM_SRC": _ABSENT, **env_extra}
    # The parent run may itself be inside this file's suite; -p no:cacheprovider
    # keeps the child from writing to the shared cache dir.
    return subprocess.run(
        [sys.executable, "-m", "pytest", _TARGET, "-q", "-p", "no:cacheprovider"],
        cwd=_REPO_ROOT, env=env, capture_output=True, text=True,
    )


def test_absent_platform_is_a_skip_by_default():
    """A laptop without the platform checkout still gets a green suite."""
    proc = _run({"REQUIRE_PLATFORM_CONTRACT": ""})

    assert proc.returncode == 0, proc.stdout[-3000:]
    assert "skipped" in proc.stdout


def test_absent_platform_is_a_HARD_FAILURE_when_the_gate_is_required():
    """The property the CI job depends on: required + absent must be RED."""
    proc = _run({"REQUIRE_PLATFORM_CONTRACT": "1"})

    assert proc.returncode != 0, (
        "REQUIRE_PLATFORM_CONTRACT=1 with no platform checkout exited 0 — the "
        "CI gate would report green while gating nothing.\n" + proc.stdout[-3000:]
    )
    combined = proc.stdout + proc.stderr
    assert "REQUIRE_PLATFORM_CONTRACT=1" in combined     # says WHY it is red
    assert "skipped" not in combined                     # and did not skip anything
