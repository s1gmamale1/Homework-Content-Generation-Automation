"""Whole-process-tree kill via psutil — one code path for Windows (dev) and
Linux (prod/k8s). `proc.kill()` only kills the direct child; provider CLIs
(node for claude/gemini, python for kimi) can spawn helpers that would orphan
and keep burning tokens after a cancel. We suspend the parent first so it can't
spawn new children during the kill (closes the snapshot window), then sweep all
descendants, kill them, and reap."""

from __future__ import annotations

import psutil
from loguru import logger


def kill_tree(pid: int, *, wait_timeout: float = 3.0) -> None:
    """Kill `pid` and every descendant. Best-effort and exception-safe: a
    process that's already gone is fine. Synchronous (callers are in await-free
    cancel handlers); may block up to `wait_timeout` reaping."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        parent.suspend()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        descendants = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    victims = [*descendants, parent]
    for p in victims:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            logger.warning(f"kill_tree: access denied killing pid={p.pid}")
    gone, alive = psutil.wait_procs(victims, timeout=wait_timeout)
    if alive:
        logger.warning(f"kill_tree: {len(alive)} process(es) survived kill: {[p.pid for p in alive]}")
