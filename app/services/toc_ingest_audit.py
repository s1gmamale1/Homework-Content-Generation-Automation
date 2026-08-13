"""Deterministic data-quality audit run once, at book ingestion, over the
extracted TOC + the PDF's text layer.

Both classes of defect this module catches were measured in a live 254-lesson
run, where they surfaced only at GENERATION time — after a worker had claimed
the lesson, fetched the book and spent model calls:

  1. **Inverted page ranges.** Three lessons died with
     ``lesson.extract (vision): cannot scope page range 35-34 of source.pdf``
     (also 50-49 and 72-71). A fleet-wide sweep of ``toc_entries`` found 8
     inverted rows (``page_end < page_start``) out of 9,634, confined to two
     books (`chizmachilik g8 ru` = 5, `ona-tili g6 uz` = 3); **5 of the 8 are
     exactly off-by-one** (``page_end == page_start - 1``).
  2. **Scanned PDFs with no text layer.** Twelve lessons across five different
     hosts died with ``lesson.extract: sparse text layer (1 chars/page) —
     likely scanned and no page range``. Five hosts hitting the same book
     proves it is a property of the BOOK, not the host — yet every lesson
     rediscovered it independently, burning a claim and a book fetch each time.

Policy encoded here (the "why", so it can be argued with rather than guessed at):

* **Off-by-one inversions are REPAIRED to ``page_end = page_start``.** The
  extractor gets ``page_start`` from the printed contents page but has to
  DERIVE ``page_end``, and the natural derivation is "the next entry's start
  minus one". When two consecutive rows share a start page — two short lessons
  printed on one page, which is exactly what a technical-drawing and a
  mother-tongue textbook are full of — that derivation yields
  ``page_start - 1``. The lesson really is a single-page lesson, so ``[p, p]``
  is the minimal, conservative claim. Swapping the two numbers instead would
  assert the lesson covers ``page_start - 1`` as well — a page that belongs to
  the PREVIOUS lesson — which is how you get a packet generated from the wrong
  source pages (see ROADMAP R27 for what that costs).
* **Any deeper inversion is SURFACED, never guessed.** 3 of the 8 live rows are
  not off-by-one; a transposition, a mis-read digit and a swapped pair are all
  plausible and indistinguishable from here. Repairing on a guess risks the R27
  failure mode (correct-looking homework built from pages that aren't the
  lesson), so those rows go to the operator with the row named.
* **A NULL page range is LEGITIMATE and is never repaired or rejected.** The
  column is nullable by design (``app/models/toc_entry.py``), the extraction
  schema keeps it ``Optional`` (``app/schemas/toc.py``), ``bulk_create`` has an
  explicit ordering rule for page-less rows, ``toc_classifier`` handles ``None``
  on both bounds, and the generation path only needs a range for two branches
  (oversize-book subsetting and scanned-book vision) — a normal book is
  extracted by locating the lesson by title in the whole-book text. Real
  page-less rows are chapter umbrellas, end-matter (Ответы / Mundarija /
  Тестовые) and the final entry, which has no following entry to derive an end
  from (ROADMAP R27 names one: `List of Irregular Verbs`, page 158, NULL end).
  So NULLs are counted, not "fixed".
* **A sparse text layer alone does NOT change the book's status.** A scanned
  book is a supported book: the pipeline vision-attaches a page window per
  lesson and reads it. Blocking it would silently drop support that was
  deliberately built (worklogs 0070/0072/0094).
* **The one BLOCKING combination is sparse-text × a lesson row with no page
  range.** That conjunction is the exact error string the 12 failures carried:
  with no text layer the extract MUST go to vision, and vision needs a page
  window to carve. Such a lesson cannot succeed, at any host, on any retry —
  so it is worth one operator look (``toc_review``) rather than N wasted
  claims. It is scoped to rows the batch launcher would actually launch
  (``toc_classifier`` class ``lesson`` — see ``batch.py``'s default
  ``include_classes={"lesson"}``), so page-less end-matter never trips it.

Deliberately dependency-light in the same spirit as ``pdf_lang.py``: pypdf +
stdlib + ``settings`` (for the shared density floor) + the standalone
``toc_classifier``. No DB, no FastAPI, no ``app.services.agent`` — so the
offline audit script can import it without dragging in the CLI machinery.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Optional, Sequence

from app.config import settings
from app.services.toc_classifier import LESSON, classify_entries

# Bounded probe: never read more than this many pages, however big the book.
# Evenly spread across the WHOLE book, not the front — a text-bearing cover +
# front matter over an image-only body is the exact masking bug documented on
# `agent._toc_pages_scanned` (3317 chars over 8 text pages reads as 414/page
# while the same text over the 55 scanned pages is 60/page).
SAMPLE_MAX_PAGES = 24

# Per-page char cap, so one pathological page cannot blow up memory. Only ever
# clips pages that are already an order of magnitude above the density floor,
# so it cannot turn a dense book sparse.
PER_PAGE_CHAR_CAP = 4_000

# How many offending rows to name in the persisted detail before summarising
# the rest as "(+N more)". Mirrors `settings.extract_coverage_max_items`'s
# reason for existing: a detail string is for a human, not a dump.
MAX_ROWS_NAMED = 10

# `agent.validate_toc` truncates its detail at 1000 chars; match it so the
# merged `books.toc_validation_detail` stays a readable paragraph.
MAX_DETAIL_CHARS = 1000


class Row(NamedTuple):
    """Normalised view of one TOC row.

    Built with ``getattr(..., default)`` so this module accepts anything TOC
    shaped — ``TOCEntryExtracted`` (pre-persist), ORM ``TOCEntry`` rows (the
    offline script), or the partial namespaces the extractor tests use.
    """

    index: int
    section_title: str
    page_start: Optional[int]
    page_end: Optional[int]


def normalise(entries: Iterable[Any]) -> list[Row]:
    return [
        Row(
            index=i,
            section_title=(getattr(e, "section_title", "") or ""),
            page_start=getattr(e, "page_start", None),
            page_end=getattr(e, "page_end", None),
        )
        for i, e in enumerate(entries)
    ]


# ─────────────────────────── page ranges ───────────────────────────

OFF_BY_ONE = "off_by_one"
INVERTED = "inverted"
NONPOSITIVE = "nonpositive"


@dataclass(frozen=True)
class PageRangeIssue:
    """One row whose printed page range cannot be true as extracted."""

    index: int
    section_title: str
    page_start: int
    page_end: int
    kind: str                      # OFF_BY_ONE | INVERTED | NONPOSITIVE
    repaired_to: Optional[int]     # new page_end when repaired, else None

    @property
    def repairable(self) -> bool:
        return self.kind == OFF_BY_ONE

    def describe(self) -> str:
        title = (self.section_title or "?").strip()[:60]
        base = f"{title!r} pages {self.page_start}-{self.page_end}"
        if self.repaired_to is not None:
            return f"{base} → repaired to {self.page_start}-{self.repaired_to} (single-page lesson)"
        if self.kind == NONPOSITIVE:
            return f"{base} (page numbers must be >= 1)"
        return f"{base} (end before start, not an off-by-one — needs a human)"


def audit_page_ranges(entries: Sequence[Any], *, repair: bool = True) -> list[PageRangeIssue]:
    """Find — and, for the off-by-one case only, repair — impossible page ranges.

    With ``repair=True`` (the ingestion default) an off-by-one row has its
    ``page_end`` set to ``page_start`` **in place on the caller's entry
    object**, so the repaired value is what gets persisted. Every repair is
    reported back, so nothing is changed silently.

    ``repair=False`` is the read-only mode the offline audit script uses.

    Rows with a NULL bound are skipped entirely — see the module docstring.
    """
    rows = normalise(entries)
    issues: list[PageRangeIssue] = []
    for row in rows:
        ps, pe = row.page_start, row.page_end
        if ps is None or pe is None:
            continue                      # legitimate: nothing to check
        if ps <= 0 or pe <= 0:
            issues.append(PageRangeIssue(row.index, row.section_title, ps, pe, NONPOSITIVE, None))
            continue
        if pe >= ps:
            continue                      # well-formed
        if pe == ps - 1:
            if repair:
                entries[row.index].page_end = ps
            issues.append(
                PageRangeIssue(row.index, row.section_title, ps, pe, OFF_BY_ONE, ps if repair else None)
            )
        else:
            issues.append(PageRangeIssue(row.index, row.section_title, ps, pe, INVERTED, None))
    return issues


# ─────────────────────────── text layer ───────────────────────────


@dataclass(frozen=True)
class TextLayerProbe:
    """Bounded chars-per-page measurement of a PDF's text layer."""

    probed: bool          # False = the PDF could not be opened at all
    total_pages: int
    sampled_pages: int    # pages ATTEMPTED, not pages that yielded text
    chars: int

    @property
    def chars_per_page(self) -> float:
        if self.sampled_pages <= 0:
            return 0.0
        return self.chars / self.sampled_pages

    @property
    def is_sparse(self) -> bool:
        """Mirrors ``agent.extract_text_is_too_sparse`` (same
        ``settings.extract_min_chars_per_page`` floor, same comparison) so the
        ingestion verdict and the generation-time gate cannot drift apart.
        Never fires when nothing could be sampled — an unreadable probe is
        "don't know", not "scanned"."""
        if not self.probed or self.sampled_pages <= 0:
            return False
        return self.chars_per_page < settings.extract_min_chars_per_page

    def describe(self) -> str:
        if not self.probed:
            return "text layer not probed (PDF unreadable)"
        return (
            f"{self.chars_per_page:.0f} chars/page over {self.sampled_pages} "
            f"sampled page(s) of {self.total_pages}"
        )


def _sample_indices(total_pages: int, max_pages: int) -> list[int]:
    """Evenly spread 0-based page indices across the whole book."""
    if total_pages <= 0:
        return []
    if total_pages <= max_pages:
        return list(range(total_pages))
    step = total_pages / max_pages
    seen: list[int] = []
    for k in range(max_pages):
        idx = min(total_pages - 1, int(k * step))
        if not seen or idx != seen[-1]:
            seen.append(idx)
    return seen


def probe_text_layer(pdf_path: Path, *, max_pages: int = SAMPLE_MAX_PAGES) -> TextLayerProbe:
    """Measure chars-per-page over a bounded, evenly-spread page sample.

    Never raises — an unopenable/encrypted/corrupt PDF returns
    ``probed=False`` (like ``pdf_lang.detect_pdf_script`` and
    ``agent.pdf_page_count``), because an ingest guard must not be able to fail
    an ingestion.

    The denominator is the number of pages **attempted**, deliberately: counting
    only pages that yielded text is what lets a handful of dense cover pages
    mask an image-only body (the bug ``agent._toc_pages_scanned`` exists to
    avoid). A scanned book yields text on zero pages, and zero over N is the
    answer we want.

    Cost is bounded to ``max_pages`` page extractions regardless of book size —
    unlike the generation path's whole-book read, which WISHLIST
    ``extract-perf-1`` measured at ~106 s for a 352-page scan.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        total_pages = len(reader.pages)
    except Exception:  # noqa: BLE001 — an unreadable PDF is "unknown", not a crash
        return TextLayerProbe(probed=False, total_pages=0, sampled_pages=0, chars=0)

    indices = _sample_indices(total_pages, max_pages)
    chars = 0
    for idx in indices:
        try:
            text = reader.pages[idx].extract_text() or ""
        except Exception:  # noqa: BLE001 — one bad content stream must not sink the sample
            text = ""
        chars += len(text.strip()[:PER_PAGE_CHAR_CAP])
    return TextLayerProbe(
        probed=True, total_pages=total_pages, sampled_pages=len(indices), chars=chars
    )


# ─────────────────────────── combined audit ───────────────────────────


@dataclass(frozen=True)
class IngestAudit:
    """What the ingest-time guards found for one book."""

    probe: TextLayerProbe
    issues: list[PageRangeIssue] = field(default_factory=list)
    entries_total: int = 0
    no_range_total: int = 0        # rows with a NULL page_start or page_end
    no_range_lessons: int = 0      # ...of which the launcher would launch
    repairs: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.repairs or self.blocking or self.advisory)

    @property
    def detail(self) -> Optional[str]:
        """One human paragraph for ``books.toc_validation_detail`` — blocking
        first (that's what the operator must act on), then repairs, then
        advisories. ``None`` when the book is clean, so a healthy ingestion
        writes nothing."""
        lines = [*self.blocking, *self.repairs, *self.advisory]
        if not lines:
            return None
        return "; ".join(f"ingest: {line}" for line in lines)[:MAX_DETAIL_CHARS]

    @property
    def summary(self) -> str:
        """Always-loggable one-liner, findings or not."""
        return (
            f"entries={self.entries_total} "
            f"page_range_issues={len(self.issues)} repaired={len(self.repairs)} "
            f"no_page_range={self.no_range_total} (lessons={self.no_range_lessons}) "
            f"text_layer=[{self.probe.describe()}] sparse={self.probe.is_sparse}"
        )


def _name_rows(issues: Sequence[PageRangeIssue]) -> str:
    named = [i.describe() for i in issues[:MAX_ROWS_NAMED]]
    extra = len(issues) - len(named)
    if extra > 0:
        named.append(f"(+{extra} more)")
    return "; ".join(named)


def audit_book(
    entries: Sequence[Any], pdf_path: Path, *, repair: bool = True
) -> IngestAudit:
    """Run every ingest-time guard for one book and classify the findings.

    ``blocking`` findings are the ones worth stopping for: they name a defect
    that is *certain* to fail at generation, for every host, on every retry.
    ``advisory`` findings are recorded but change nothing — a scanned book is
    a supported book.
    """
    rows = normalise(entries)
    issues = audit_page_ranges(entries, repair=repair)
    try:
        probe = probe_text_layer(pdf_path)
    except Exception:  # noqa: BLE001 — defence in depth; probe_text_layer already swallows
        probe = TextLayerProbe(probed=False, total_pages=0, sampled_pages=0, chars=0)

    page_less = [r for r in rows if r.page_start is None or r.page_end is None]
    no_range_lessons = 0
    if page_less and probe.is_sparse:
        # Only the sparse branch needs the class breakdown, and classification
        # is O(n^2) in the containment pass — don't pay for it otherwise.
        classes = classify_entries(rows)
        no_range_lessons = sum(1 for r in page_less if classes[r.index] == LESSON)

    repairs: list[str] = []
    blocking: list[str] = []
    advisory: list[str] = []

    repaired = [i for i in issues if i.repaired_to is not None]
    if repaired:
        repairs.append(
            f"repaired {len(repaired)} off-by-one page range(s): {_name_rows(repaired)}"
        )
    unrepaired = [i for i in issues if i.repaired_to is None]
    if unrepaired:
        blocking.append(
            f"{len(unrepaired)} impossible page range(s) left as extracted: {_name_rows(unrepaired)} "
            "— fix with PATCH /books/{book_id}/toc/{entry_id}, then accept the TOC"
        )

    if probe.is_sparse:
        density = probe.describe()
        if no_range_lessons:
            blocking.append(
                f"scanned/no text layer ({density}) AND {no_range_lessons} lesson row(s) "
                "have no page range — a scanned lesson is extracted by attaching its page "
                "window, so those lessons cannot succeed at any host; set their page ranges "
                "with PATCH /books/{book_id}/toc/{entry_id}, or accept to launch anyway"
            )
        else:
            advisory.append(
                f"scanned/no text layer ({density}) — every lesson will extract via the "
                "vision path using its page range; the book is usable, not rejected"
            )

    return IngestAudit(
        probe=probe,
        issues=issues,
        entries_total=len(rows),
        no_range_total=len(page_less),
        no_range_lessons=no_range_lessons,
        repairs=repairs,
        blocking=blocking,
        advisory=advisory,
    )
