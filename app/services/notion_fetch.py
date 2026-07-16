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


def _map_subject_for_language(title: str, language: str) -> str | None:
    """Notion subject-page title → app subject key for the given language, or
    None if the title doesn't match any registered subject.

    ``language`` must be ``"uz"``, ``"ru"``, or ``"en"`` — it selects which
    keyword set is consulted (see ``subjects.notion_keyword_pairs``).

    Excluded titles (EXCLUDED_KEYWORDS) are rejected before keyword matching
    regardless of language — they only apply to the Uzbek set, but guarding them
    unconditionally is safe (they're Latin-script words absent from ru/en sets)."""
    keyword_pairs = subjects.notion_keyword_pairs(language)
    folded = _fold(title)
    for excluded in subjects.EXCLUDED_KEYWORDS:
        if excluded in folded:
            return None
    for keyword, app_subject in keyword_pairs:
        if keyword in folded:
            return app_subject
    return None


def _map_subject(title: str) -> str | None:
    """Notion subject-page title -> app subject key, or None if unsupported.

    Uzbek-language wrapper around ``_map_subject_for_language``. Excluded titles
    (e.g. "Jismoniy tarbiya"/PE, "Axloqiy tarbiya"/Ethics) are rejected BEFORE
    keyword matching — they contain the bare "tarbiya" keyword (Upbringing) as a
    substring and would otherwise mis-map to it."""
    return _map_subject_for_language(title, "uz")


_TEXTBOOK_MARKERS = ("darslik", "textbook", "учебник")
_WORKBOOK_MARKERS = ("ish daftari", "ishchi daftar", "workbook", "daftar", "рабочая тетрадь", "тетрадь")

# Bot/source handles (e.g. "(@elektron_darslikbot)") named in a filename to
# credit the download source. Must be stripped BEFORE marker matching: the
# handle "@elektron_darslikbot" itself contains "darslik", which used to make
# any workbook that named it (e.g. "mashq daftari (@elektron_darslikbot).pdf")
# misrank as a textbook (fetch-2 regression).
_HANDLE_RE = re.compile(r"\(?@[a-z0-9_]+\)?")


def _pdf_name(block: dict) -> str:
    """Folded, lower-cased filename of a pdf/file block (``""`` when unnamed)."""
    payload = block.get(block.get("type"), {})
    return _fold(payload.get("name") or "")


def _pdf_rank(name: str) -> int:
    """Selection rank for a PDF filename — LOWER is preferred. A `darslik`
    (textbook) beats a neutral PDF beats an `ish daftari` (workbook), so a
    workbook listed first no longer becomes the batch's 'textbook' (fetch-2).

    ``name`` is expected already `_fold`-ed. Bot/source handles are stripped
    first, and workbook markers are checked BEFORE textbook markers, so a
    workbook name that happens to retain a residual textbook-marker fragment
    (e.g. from a bot handle) still ranks as a workbook."""
    stripped = _HANDLE_RE.sub("", name)
    if any(m in stripped for m in _WORKBOOK_MARKERS):
        return 2
    if any(m in stripped for m in _TEXTBOOK_MARKERS):
        return 0
    return 1


def _first_pdf_block(blocks: list[dict]) -> dict | None:
    """Best textbook PDF block, else None. Prefers a `darslik` over an `ish
    daftari` when a subject page attaches both; ties broken by page order. A
    `pdf` block is inherently a PDF; a `file` block needs a `.pdf` filename (a
    page may also attach a cover image / .docx, which must NOT be the textbook)."""
    candidates: list[tuple[int, int, dict]] = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if not _url_from_block(b):
            continue
        if t == "pdf":
            candidates.append((_pdf_rank(_pdf_name(b)), i, b))
        elif t == "file":
            name = (b.get("file", {}).get("name") or "").lower()
            if name.endswith(".pdf"):
                candidates.append((_pdf_rank(_pdf_name(b)), i, b))
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c[0], c[1]))[2]


def _url_from_block(block: dict) -> str | None:
    """Resolve a file/pdf block's URL (Notion-hosted signed OR external)."""
    payload = block.get(block.get("type"), {})
    return (payload.get("file") or {}).get("url") or (payload.get("external") or {}).get("url")


_SINF_RE = re.compile(r"-\s*sinf\b", re.IGNORECASE)

_LANG_CONTAINER_RE: dict[str, re.Pattern[str]] = {
    "uz": _SINF_RE,
    "ru": re.compile(r"-\s*(класс|klass)\b", re.I),
    # English containers must be explicitly named by the operator ("english" /
    # "inglizcha" / "ingliz").  The bare word "grade" is dropped: it is a
    # common cognate that appears in Uzbek container names (e.g. "9 - grade
    # subjects") and would produce phantom English editions for any subject
    # whose title is shared across language trees (operator convention).
    "en": re.compile(r"-\s*(english|inglizcha|ingliz)\b", re.I),
}


def _subjects_under(client, grade_page_id: str, container_re: re.Pattern[str], language: str) -> list[dict]:
    """Subjects under the grade's language-specific container child.

    Finds the container whose title matches ``container_re``, then returns one
    dict per subject page under it: ``{notion_title, page_id, app_subject|None,
    has_textbook}``.  Returns ``[]`` when no matching container is found.
    """
    container = next(
        (c for c in client.get_child_pages(grade_page_id) if container_re.search(c["title"])),
        None,
    )
    if container is None:
        return []
    out = []
    for s in client.get_child_pages(container["id"]):
        blocks = client.get_block_children(s["id"])
        out.append({
            "notion_title": s["title"].strip(),
            "page_id": s["id"],
            "app_subject": _map_subject_for_language(s["title"], language),
            "has_textbook": _first_pdf_block(blocks) is not None,
        })
    return out


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
    {notion_title, page_id, app_subject|None, has_textbook}.

    Backward-compat wrapper around ``_subjects_under`` for existing callers."""
    return _subjects_under(client, grade_page_id, _SINF_RE, "uz")


def list_subjects_for_language(client, grade_page_id: str, language: str) -> list[dict]:
    """Subjects under the grade's container for the given language (uz/ru/en).

    Thin wrapper around ``_subjects_under`` using the per-language container
    regex from ``_LANG_CONTAINER_RE``.  Returns ``[]`` when the container is
    absent (e.g. English is unavailable until the page is created)."""
    return _subjects_under(client, grade_page_id, _LANG_CONTAINER_RE[language], language)


def available_languages(client, grade_page_id: str) -> dict[str, dict[str, dict]]:
    """Detect which languages are available per subject under this grade page.

    Crawls all three containers (uz/ru/en) and returns a nested mapping:
    ``{app_subject: {lang: {"page_id": <first part>, "has_textbook": …, "parts": [{page_id,title,has_textbook}, …]}}}``.

    Inclusion rule: a subject/language pair is recorded only when
    - the language's container child EXISTS under the grade page, AND
    - the subject page maps to a non-None ``app_subject``, AND
    - the subject page has at least one textbook PDF (``has_textbook=True``).

    Same-subject parts (e.g. multi-volume textbooks) are preserved in ``parts``,
    not collapsed.

    A language whose container is absent contributes nothing (e.g. English is
    simply absent today — UI can treat it as unavailable)."""
    result: dict[str, dict[str, dict]] = {}
    for lang in ("uz", "ru", "en"):
        for entry in _subjects_under(client, grade_page_id, _LANG_CONTAINER_RE[lang], lang):
            app_subject = entry["app_subject"]
            if app_subject is None:
                continue
            if not entry["has_textbook"]:
                continue
            lang_map = result.setdefault(app_subject, {})
            # Multi-part subjects (e.g. "Matematika 1-qism"/"2-qism") share an
            # app_subject. Accumulate every part in `parts` instead of letting the
            # last page clobber the first — the FE resolves the correct part from
            # this list (notion-multipart-subject-clobber-1). Top-level page_id /
            # has_textbook are kept (page_id = the FIRST part) for backward-compat.
            slot = lang_map.setdefault(
                lang, {"page_id": entry["page_id"], "has_textbook": True, "parts": []}
            )
            slot["parts"].append({
                "page_id": entry["page_id"],
                "title": entry["notion_title"],
                "has_textbook": entry["has_textbook"],
            })
    return result


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
