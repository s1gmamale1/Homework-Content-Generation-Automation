"""Browse the Notion 'Lessons' tree and download the textbook attached to a
subject page, so a book can be generated without a manual upload. Read-only
crawl + a single file download. sinf-only (Uzbek) for v1."""

from __future__ import annotations

import re

import httpx

from app.config import settings
from app.services import subjects

# Folded-substring map (folded-keyword, app-subject), LONGEST keyword first so a
# double-hit (e.g. "jismoniy tarbiya" vs "tarbiya") resolves deterministically.
# Derived from the single source of truth (app/services/subjects.py).
_SUBJECT_KEYWORDS: list[tuple[str, str]] = subjects.notion_keyword_pairs()

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


class NoTextbook(Exception):
    """Subject page has no downloadable PDF block."""


class TextbookTooLarge(Exception):
    """Attachment exceeds the ingest ceiling (settings.max_file_mb, shared with
    upload). Raised from the old hardcoded 20 MB Gemini-TOC limit and tied to the
    upload cap so the two can't drift: TOC extraction handles >20 MB via local
    pypdf text, so there's no reason to cap fetch below upload."""


def download_textbook(client, subject_page_id: str) -> tuple[bytes, str]:
    """Resolve the subject page's first PDF block, reject files larger than the
    upload cap (settings.max_file_mb), return (bytes, filename).

    Notion attachment URLs are S3 links presigned for GET only -- a HEAD request
    against them 403s (the signature covers the GET method, not HEAD). So we open
    a STREAMING GET, read Content-Length from the response headers, and reject an
    oversize file BEFORE consuming its body; a post-read length check covers the
    rare case where the header is absent.
    """
    max_bytes = settings.max_file_mb * 1024 * 1024
    block = _first_pdf_block(client.get_block_children(subject_page_id))
    if block is None:
        raise NoTextbook(subject_page_id)
    url = _url_from_block(block)
    payload = block.get(block.get("type"), {})
    filename = (payload.get("name") or "textbook.pdf").strip() or "textbook.pdf"
    with httpx.Client(timeout=60.0) as http:
        with http.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            size = int(resp.headers.get("Content-Length") or 0)
            if size > max_bytes:
                raise TextbookTooLarge(f"{size / 1048576:.1f} MB > {settings.max_file_mb} MB")
            body = resp.read()
    if len(body) > max_bytes:        # fallback when Content-Length absent
        raise TextbookTooLarge(f"{len(body) / 1048576:.1f} MB > {settings.max_file_mb} MB")
    return body, filename
