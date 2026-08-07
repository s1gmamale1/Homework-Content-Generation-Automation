"""Extract-fidelity audit (offline): grades an already-generated `extract`
phase output against the textbook pages it was written from.

Loads exactly what the live guard's verify call sees for one job — the
extract markdown, the page-window source text, and (separately) the whole
book text the extract was actually generated from — plus a structured
per-claim verdict shape a later task's LLM call fills in.

Mirrors `app.services.teaching_audit`'s `AuditInputs` / `load_audit_inputs`
pattern, including its FAIL-LOUD posture: missing job/book/TOC row, NULL
page range, missing PDF, or empty page text all raise
`ExtractFidelityAuditError` — never silently degrade or return a partial
object.

This module is data-loading + result models only. No LLM calls, no prompts,
no orchestration — those are later tasks in this plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel

from app.services import agent

# Extra pages read on each side of the TOC's recorded range, matching
# `teaching_audit._PAGE_WINDOW_MARGIN` (measured -3..+2 TOC page-offset
# spread). A narrower window (e.g. margin=1, the `pipeline._verify_source_for_
# section` window) measures guard blindness, not a true drift base rate: a
# window that misses the lesson body makes every claim read `unsupported`,
# including a planted mutation, which would pass a calibration gate on a
# broken instrument.
_PAGE_WINDOW_MARGIN = 4

_WHITESPACE_RE = re.compile(r"\s+")


class ExtractFidelityAuditError(RuntimeError):
    """Any unrecoverable audit-input load failure (missing data)."""


def _normalize_span(s: str) -> str:
    """Prose normalization for claim-span substring matching: casefold,
    collapse whitespace runs to a single space, strip. Deliberately NOT
    `agent._normalize_expr`-style — that function is math-specific (maps
    `·*×`->`*`, `−–—`->`-`, `÷`->`/`, strips ALL spaces), which is wrong for
    prose. Match semantics (defined once, here, for Task 2's substring
    re-grounding and Task 3's planted-span detection): compare after this
    normalization, exact normalized substring only — no fuzzy matching, which
    would silently downgrade real drift."""
    return _WHITESPACE_RE.sub(" ", s.casefold()).strip()


ClaimStatus = Literal["contradicts", "unsupported", "ok"]
ClaimType = Literal["name", "date", "number", "definition", "quote", "term", "other"]


class ClaimVerdict(BaseModel):
    """One claim's grading against the source text.

    `claim_span` is the exact substring of the extract the verdict is
    about — carried verbatim so downstream consumers (Task 2's
    `reground_unsupported` substring match, Task 3's paired planted-span
    detection) don't each invent their own extraction. Match semantics: see
    `_normalize_span`.
    """

    claim_span: str
    claim_type: ClaimType
    status: ClaimStatus


class ExtractFidelityReport(BaseModel):
    """Per-status claim counts for one extract-fidelity audit."""

    ok_count: int = 0
    contradicts_count: int = 0
    unsupported_count: int = 0

    @property
    def total_count(self) -> int:
        return self.ok_count + self.contradicts_count + self.unsupported_count

    @classmethod
    def from_claims(cls, claims: list[ClaimVerdict]) -> "ExtractFidelityReport":
        """Aggregate a claim list into per-status counts. An empty list
        aggregates to zero drift, not an error."""
        counts = {"ok": 0, "contradicts": 0, "unsupported": 0}
        for c in claims:
            counts[c.status] += 1
        return cls(
            ok_count=counts["ok"],
            contradicts_count=counts["contradicts"],
            unsupported_count=counts["unsupported"],
        )


@dataclass(frozen=True)
class ExtractAuditInputs:
    job_id: str
    book_id: str
    subject: str
    family: str
    grade: Optional[str]
    source_language: str
    output_language: str
    lesson_title: str
    page_start: int
    page_end: int
    extract_md: str
    source_text: str
    whole_book_text: str


async def load_extract_audit_inputs(
    job_id: UUID | str, *, whole_book_text: Optional[str] = None
) -> ExtractAuditInputs:
    """Load everything the extract-fidelity audit needs for one job. FAILS
    LOUD on any gap — missing job/book/TOC row, NULL page range, missing
    PDF, empty page text, or no completed `extract` phase output.

    `whole_book_text`, when given, is used instead of re-reading the PDF —
    reading whole-book text costs seconds per call and a later task audits
    many lessons drawn from a handful of books, so the caller caches one
    read per `book_id`. When `None`, it's read via `agent.read_whole_book_text`.
    """
    from app.db import SessionLocal
    from app.models import Book, HomeworkJob, TOCEntry
    from app.repositories import phase_outputs as phase_repo
    from app.services import storage
    from app.services import subjects

    async with SessionLocal() as session:
        job = await session.get(HomeworkJob, job_id)
        if job is None:
            raise ExtractFidelityAuditError(f"homework_jobs row {job_id!r} not found")
        book = await session.get(Book, job.book_id)
        if book is None:
            raise ExtractFidelityAuditError(f"books row {job.book_id!r} not found")
        toc = await session.get(TOCEntry, job.toc_entry_id)
        if toc is None:
            raise ExtractFidelityAuditError(f"toc_entries row {job.toc_entry_id!r} not found")
        if toc.page_start is None or toc.page_end is None:
            raise ExtractFidelityAuditError(
                f"TOC entry {toc.id} has no page range (page_start={toc.page_start!r}, "
                f"page_end={toc.page_end!r}) — cannot derive a fidelity audit"
            )
        rows = await phase_repo.list_for_job(session, job.id)
        extract_row = next(
            (
                r
                for r in rows
                if r.phase_name == "extract" and r.status == "done" and (r.output_md or "").strip()
            ),
            None,
        )
        if extract_row is None:
            raise ExtractFidelityAuditError(
                f"job {job_id} has no completed 'extract' phase output to audit"
            )
        extract_md = extract_row.output_md
        subject, grade = job.subject, book.grade
        source_language, output_language = book.source_language, job.output_language
        lesson_title, book_id = toc.section_title, str(job.book_id)
        page_start, page_end = toc.page_start, toc.page_end

    subject_def = subjects.REGISTRY.get(subject)
    family = subject_def.family if subject_def is not None else "default"

    pdf_path = storage.book_pdf_path(book_id)
    if not pdf_path.exists():
        raise ExtractFidelityAuditError(f"source PDF missing: {pdf_path}")
    source_text = agent.read_page_range_text(
        pdf_path, page_start, page_end, margin=_PAGE_WINDOW_MARGIN
    )
    if not source_text:
        raise ExtractFidelityAuditError(
            f"pages {page_start}-{page_end} (±{_PAGE_WINDOW_MARGIN}) of {pdf_path.name} "
            f"yielded no text (image-only scan?) — cannot audit extract fidelity"
        )

    if whole_book_text is None:
        whole_book_text = agent.read_whole_book_text(pdf_path)
    if not whole_book_text:
        raise ExtractFidelityAuditError(
            f"{pdf_path.name} yielded no whole-book text — cannot audit extract fidelity"
        )

    return ExtractAuditInputs(
        job_id=str(job_id),
        book_id=book_id,
        subject=subject,
        family=family,
        grade=grade,
        source_language=source_language,
        output_language=output_language,
        lesson_title=lesson_title,
        page_start=page_start,
        page_end=page_end,
        extract_md=extract_md,
        source_text=source_text,
        whole_book_text=whole_book_text,
    )
