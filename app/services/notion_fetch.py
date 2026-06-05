"""Browse the Notion 'Lessons' tree and download the textbook attached to a
subject page, so a book can be generated without a manual upload. Read-only
crawl + a single file download. sinf-only (Uzbek) for v1."""

from __future__ import annotations

import re

# Folded-substring map, LONGEST keyword first so a double-hit is deterministic.
# "matematika" is intentionally absent (lower-grade math != the app's algebra).
_SUBJECT_KEYWORDS: list[tuple[str, str]] = [
    ("ozbekiston tarixi", "history"),
    ("jahon tarixi", "history"),
    ("geometriya", "geometriya-g7-11"),
    ("biolog", "biology"),
    ("algebra", "math-algebra"),
    ("ingliz", "english"),
    ("fizika", "physics"),
    ("kimyo", "kimyo-g7-11"),
    ("tarix", "history"),
]

_APOSTROPHES = "'\u2018\u2019\u02bb`"

def _fold(s: str) -> str:
    return s.lower().translate({ord(c): None for c in _APOSTROPHES})


def _map_subject(title: str) -> str | None:
    """Notion subject-page title -> app subject key, or None if unsupported."""
    folded = _fold(title)
    for keyword, app_subject in _SUBJECT_KEYWORDS:
        if keyword in folded:
            return app_subject
    return None


def _first_pdf_block(blocks: list[dict]) -> dict | None:
    """First textbook PDF in page order, else None. A `pdf` block is inherently a
    PDF; a `file` block must have a `.pdf` filename - a subject page may also attach
    a cover image / .docx, which must NOT be fed to the extractor as a 'textbook'."""
    for b in blocks:
        t = b.get("type")
        if t == "pdf" and _url_from_block(b):
            return b
        if t == "file" and _url_from_block(b):
            name = (b.get("file", {}).get("name") or "").lower()
            if name.endswith(".pdf"):
                return b
    return None


def _url_from_block(block: dict) -> str | None:
    """Resolve a file/pdf block's URL (Notion-hosted signed OR external)."""
    payload = block.get(block.get("type"), {})
    return (payload.get("file") or {}).get("url") or (payload.get("external") or {}).get("url")


_SINF_RE = re.compile(r"-\s*sinf\b", re.IGNORECASE)


def list_grades(client, lessons_root: str) -> list[dict]:
    """Grade pages under the Lessons root, excluding the 'Rules' page."""
    out = []
    for g in client.get_child_pages(lessons_root):
        if _fold(g["title"]).strip() == "rules":
            continue
        out.append({"title": g["title"].strip(), "page_id": g["id"]})
    return out


def list_subjects(client, grade_page_id: str) -> list[dict]:
    """Subjects under the grade's Uzbek 'N - sinf' child (klass ignored). Each:
    {notion_title, page_id, app_subject|None, has_textbook}."""
    sinf = next((c for c in client.get_child_pages(grade_page_id)
                 if _SINF_RE.search(c["title"])), None)
    if sinf is None:
        return []
    out = []
    for s in client.get_child_pages(sinf["id"]):
        blocks = client.get_block_children(s["id"])
        out.append({
            "notion_title": s["title"].strip(),
            "page_id": s["id"],
            "app_subject": _map_subject(s["title"]),
            "has_textbook": _first_pdf_block(blocks) is not None,
        })
    return out
