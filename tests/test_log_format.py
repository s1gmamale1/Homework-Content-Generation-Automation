"""log-hygiene-1: the shared loguru format must carry the DATE, not just the time.

Across a midnight boundary a date-less line silently mixes two days/runs in one
file (the reason the last audit needed model-discrimination + DB cross-checks).
This pins the date into `_FMT`."""
import io
import re

from loguru import logger

import app.log as applog


def test_fmt_includes_date():
    buf = io.StringIO()
    sink_id = logger.add(buf, format=applog._FMT, colorize=False, level="INFO")
    try:
        logger.info("x")
    finally:
        logger.remove(sink_id)
    line = buf.getvalue()
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ", line
    ), f"log line must start with 'YYYY-MM-DD HH:MM:SS.mmm '; got {line!r}"
