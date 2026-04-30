"""Loguru configuration. Imported once from main.py at app startup.

The same `logger` is re-imported throughout the app — loguru is a singleton,
so configuring it here applies everywhere. Use `from loguru import logger`
in any module to log.
"""

from __future__ import annotations

import sys

from loguru import logger

_FMT = (
    "<green>{time:HH:mm:ss.SSS}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)


def configure(level: str = "INFO") -> None:
    """Reset the default loguru handler and install our colored stderr sink."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=_FMT,
        colorize=True,
        enqueue=True,        # thread-safe; SSE handlers and background tasks log concurrently
        backtrace=False,     # avoid leaking variable values into logs by default
        diagnose=False,      # ditto
    )
