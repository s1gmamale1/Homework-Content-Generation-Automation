"""Lifespan version-floor auto-stamp wiring (fleet-worker-version-gate-1).

main.lifespan cannot run without a live DB, so wiring is proven by source
inspection (the established pattern from the events_bus lifespan test), and
the stamp helper's semantics are already real-DB-proven in
tests/services/test_version_floor_repo.py.
"""
from __future__ import annotations

import inspect


def test_lifespan_stamps_version_floor():
    import main

    src = inspect.getsource(main.lifespan)
    assert "raise_version_floor" in src
    # stamped before the SSE listener starts (both are startup-critical order)
    assert src.index("raise_version_floor") < src.index("start_listener")
    # guarded: an undetectable version must NOT stamp
    assert "CODE_VERSION is not None" in src
