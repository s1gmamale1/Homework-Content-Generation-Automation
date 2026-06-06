"""Best-effort grade extraction from a textbook filename.

The book.grade column is the Notion-archive key ({subject}|{grade}); a NULL
grade silently defeats archiving. Uzbek textbook filenames almost always state
the grade ("7-sinf_Algebra…"), so when the uploader omits it we derive it here.
Best-effort only: an unparseable name returns None (and the archive then
surfaces the skip rather than failing silently)."""
from __future__ import annotations

import re
from typing import Optional

# "<n> sinf|klass|класс" with optional separator; case-insensitive.
_GRADE_RE = re.compile(r"(\d{1,2})\s*[-_ ]?\s*(?:sinf|klass|класс)", re.IGNORECASE)


def derive_grade_from_filename(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    m = _GRADE_RE.search(name)
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= 11:          # supported band; reject 0, 12+
        return str(n)
    return None
