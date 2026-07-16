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


def _url_from_block(block: dict) -> str | None:
    """Resolve a file/pdf block's URL (Notion-hosted signed OR external)."""
    payload = block.get(block.get("type"), {})
    return (payload.get("file") or {}).get("url") or (payload.get("external") or {}).get("url")


def _is_pdf_block(block: dict) -> bool:
    """True if `block` is a pdf/file block that resolves to a PDF: a `pdf`
    block is inherently a PDF; a `file` block needs a `.pdf` filename
    (covers/attachments must NOT count)."""
    if not _url_from_block(block):
        return False
    t = block.get("type")
    if t == "pdf":
        return True
    if t == "file":
        name = (block.get("file", {}).get("name") or "").lower()
        return name.endswith(".pdf")
    return False


_CONTAINER_BLOCK_TYPES = {"toggle", "column_list", "column"}
_MAX_CONTAINER_DEPTH = 3  # bounds toggle/column_list/column nesting per page


def textbook_candidates(client, page_id: str) -> list[dict]:
    """Enumerate every textbook PDF reachable from `page_id`: direct blocks,
    PDFs nested inside containers (`toggle`/`column_list`/`column`, recursed
    depth-bound at ~3 levels), and PDFs living on `child_page` blocks (scanned
    the same way, but only ONE child-page level deep — no grandchildren).

    Each candidate: `{page_id, block_id, filename, rank, url}` — `page_id` is
    the page the block conceptually lives ON (the parent page for direct/
    container blocks; the CHILD page's own id for child_page-hosted PDFs).
    Block order is preserved; `rank` mirrors `_pdf_rank` so a caller can prefer
    a `darslik` over an `ish daftari` across the whole candidate set, not just
    one page's direct blocks."""
    return _walk_for_candidates(client, container_id=page_id, page_id=page_id,
                                 container_depth=0, allow_child_page=True)


def _walk_for_candidates(client, container_id: str, page_id: str,
                          container_depth: int, allow_child_page: bool) -> list[dict]:
    candidates: list[dict] = []
    for block in client.get_block_children(container_id):
        t = block.get("type")
        if _is_pdf_block(block):
            payload = block.get(t, {})
            # `filename` stays RAW (for display/return to the caller); it's
            # folded ONLY for `_pdf_rank` below (rank matching needs lower-case,
            # apostrophe-stripped text). Not reusing `_pdf_name` here because that
            # helper takes a whole block, and this loop already has the payload
            # unpacked — folding the local `filename` inline avoids a second
            # block.get(block.get("type"), {}) lookup for the same value.
            filename = payload.get("name") or ""
            candidates.append({
                "page_id": page_id,
                "block_id": block["id"],
                "filename": filename,
                "rank": _pdf_rank(_fold(filename)),
                "url": _url_from_block(block),
            })
        elif t in _CONTAINER_BLOCK_TYPES and container_depth < _MAX_CONTAINER_DEPTH:
            candidates.extend(_walk_for_candidates(
                client, container_id=block["id"], page_id=page_id,
                container_depth=container_depth + 1, allow_child_page=allow_child_page,
            ))
        elif t == "child_page" and allow_child_page:
            candidates.extend(_walk_for_candidates(
                client, container_id=block["id"], page_id=block["id"],
                container_depth=0, allow_child_page=False,
            ))
    return candidates


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
        candidates = textbook_candidates(client, s["id"])
        out.append({
            "notion_title": s["title"].strip(),
            "page_id": s["id"],
            "app_subject": _map_subject_for_language(s["title"], language),
            "has_textbook": bool(candidates),
            "candidates": candidates,
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
                "candidates": entry["candidates"],
            })
    return result


class NoTextbook(Exception):
    """Subject page has no downloadable PDF block (zero candidates at all), OR
    an explicit `block_id` selector wasn't among the page's candidates — either
    way there's nothing valid to fetch for the given inputs."""


class AmbiguousTextbook(Exception):
    """The page has more than one candidate in the best-rank tier and no
    `block_id` was given to disambiguate (e.g. a multi-part textbook like
    G11-UZ Algebra's two same-rank parts). Carries the tied candidate list —
    each `{page_id, block_id, filename, rank, url}` — so a caller (the route)
    can present the options instead of silently picking one."""

    def __init__(self, candidates: list[dict]):
        self.candidates = candidates
        super().__init__(f"{len(candidates)} equally-ranked textbook candidates — block_id required")


class TextbookTooLarge(Exception):
    """Attachment exceeds the ingest ceiling (settings.max_file_mb, shared with
    upload). Raised from the old hardcoded 20 MB Gemini-TOC limit and tied to the
    upload cap so the two can't drift: TOC extraction handles >20 MB via local
    pypdf text, so there's no reason to cap fetch below upload."""


def _select_candidate(candidates: list[dict], block_id: str | None = None) -> dict:
    """Resolve which candidate `download_textbook` fetches. `candidates` is
    the page's full `textbook_candidates(...)` list — callers must check for
    `[]` themselves (that's the `NoTextbook` "nothing attached" case, distinct
    from the cases here).

    - `block_id` given: return the matching candidate exactly, whatever its
      rank (an explicit choice overrides auto-ranking) — this also reaches a
      candidate hosted on a child_page, since `textbook_candidates` already
      flattened those into the same list. Raises `NoTextbook` when `block_id`
      isn't among `candidates` (a stale/invalid selector must not silently
      fall back to auto-selection).
    - `block_id` omitted: restrict to the BEST-rank tier (mirrors the old
      `_first_pdf_block` min-rank behavior — rank 0 `darslik` beats rank 1
      neutral beats rank 2 `ish daftari`). Exactly one candidate in that tier
      downloads outright. MORE than one raises `AmbiguousTextbook` instead of
      silently picking page order like the old code did (the approved
      behavior change for multi-part pages — BE-19 task 3)."""
    if block_id is not None:
        match = next((c for c in candidates if c["block_id"] == block_id), None)
        if match is None:
            raise NoTextbook(f"block_id {block_id!r} not among this page's textbook candidates")
        return match
    best_rank = min(c["rank"] for c in candidates)
    tier = [c for c in candidates if c["rank"] == best_rank]
    if len(tier) > 1:
        raise AmbiguousTextbook(tier)
    return tier[0]


def download_textbook(client, subject_page_id: str, block_id: str | None = None) -> tuple[bytes, str]:
    """Resolve the subject page's textbook PDF (via `textbook_candidates` +
    `_select_candidate`), reject files larger than the upload cap
    (settings.max_file_mb), return (bytes, filename).

    `block_id` selects a specific candidate explicitly (required to resolve an
    ambiguous multi-part page — see `_select_candidate`); omitted, the single
    best-rank-tier candidate is used, or `AmbiguousTextbook`/`NoTextbook` is
    raised.

    Notion attachment URLs are S3 links presigned for GET only -- a HEAD request
    against them 403s (the signature covers the GET method, not HEAD). So we open
    a STREAMING GET, read Content-Length from the response headers, and reject an
    oversize file BEFORE consuming its body; a post-read length check covers the
    rare case where the header is absent.
    """
    max_bytes = settings.max_file_mb * 1024 * 1024
    candidates = textbook_candidates(client, subject_page_id)
    if not candidates:
        raise NoTextbook(subject_page_id)
    candidate = _select_candidate(candidates, block_id=block_id)
    url = candidate["url"]
    filename = (candidate["filename"] or "textbook.pdf").strip() or "textbook.pdf"
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
