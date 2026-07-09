"""Worker vintage identity + capability blob (fleet-worker-version-gate-1).

RED-proofs:
  - blob without code_version/git_sha keys -> FE has nothing to render.
  - _worker_id without the @sha suffix -> claimed_by attribution stays blind
    (the exact gap worklog 0125 recorded).
"""
from __future__ import annotations

from unittest.mock import patch

from app.services import code_version
from app.services import worker as worker_mod


def test_capability_blob_carries_version_and_sha():
    with patch.object(code_version, "CODE_VERSION", 1234), \
         patch.object(code_version, "GIT_SHA", "abc1234"):
        blob = worker_mod._capability_blob({})
    assert blob["code_version"] == 1234
    assert blob["git_sha"] == "abc1234"
    assert "cli" in blob and "api" in blob  # existing shape untouched


def test_worker_id_carries_sha_suffix():
    with patch.object(code_version, "GIT_SHA", "abc1234"):
        wid = worker_mod._worker_id()
    assert wid.endswith("@abc1234")
    assert ":" in wid  # hostname:pid core intact


def test_worker_id_without_sha_falls_back_to_bare():
    with patch.object(code_version, "GIT_SHA", None):
        wid = worker_mod._worker_id()
    assert "@" not in wid
    assert len(wid) <= 128  # fits claimed_by/pc_id String(128)
