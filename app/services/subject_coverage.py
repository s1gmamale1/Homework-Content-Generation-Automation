"""Pure builder for the subject-coverage dashboard.

Turns already-fetched rows (books, their TOC entries, per-book job-status
counts, per-book batch links) into one `CoverageEntry` per BOOK. No DB, no
I/O — the repository layer does the fetching, this module does the shaping,
so the interesting logic is unit-testable without Postgres.

The lesson denominator is deliberately `classify_entries(...) == "lesson"`,
NOT the raw TOC row count: `toc_total` includes headers, tests, revision and
answer-key rows, so a raw count would overstate the work ("12 of 40" against
a book with only 28 real lessons). There is no SQL path for this — the
classifier is pure Python and must run per book.

The job tally is scoped to the SAME lesson rows, which is why this module
receives per-TOC-entry job statuses rather than pre-summed counts: legacy
(pre-#89, unfiltered) launches left real `done` jobs on test/revision rows,
and counting those against a lesson-only denominator would mask a failed
lesson as "Finished".

One entry per book (never per grade+subject): a grade+subject can legitimately
hold two textbooks (e.g. a uz and a ru edition), and silently picking one would
hide the other. The frontend groups these under a single subject row.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.services.toc_classifier import LESSON, classify_entries


@dataclass(frozen=True)
class CoverageEntry:
    grade: Optional[str]
    subject: str
    book_id: str
    book_status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str]
    lessons_total: int  # launchable lessons (classify_entries == "lesson")
    done: int
    running: int
    pending: int
    failed: int
    cancelled: int
    batch_id: Optional[str]
    paused: bool


_COUNTED_STATUSES = ("done", "running", "pending", "failed", "cancelled")


def _lesson_tally(
    toc_rows: list, job_status: dict[str, str]
) -> tuple[int, dict[str, int]]:
    """(launchable-lesson count, per-status tally) for ONE book.

    Both numbers are scoped to the SAME set of TOC rows — those the classifier
    calls `lesson`. This scoping is load-bearing, not tidiness: pre-#89 batch
    launches were unfiltered, so legacy books carry real `done` jobs on
    test/revision/header rows. Tallying those against a lesson-only denominator
    would let non-lesson work mask a failed lesson and report "Finished"
    (a book with 3 lessons, one failed, plus 2 done test-row jobs → done=3 of 3).
    With one shared scope, `done == lessons_total` means every LESSON is done —
    correct by construction.

    `cancelling` folds into `running`: it is an in-flight state a non-technical
    viewer should not have to reason about.
    """
    tally = {s: 0 for s in _COUNTED_STATUSES}
    if not toc_rows:
        return 0, tally
    classes = classify_entries(toc_rows)
    lesson_ids = {str(row.id) for row, cls in zip(toc_rows, classes) if cls == LESSON}
    for toc_id, status in job_status.items():
        if toc_id not in lesson_ids:
            continue  # legacy job on a test/revision/header row — not lesson work
        key = "running" if status == "cancelling" else status
        if key in tally:
            tally[key] += 1
    return len(lesson_ids), tally


def build_coverage(
    books: list,
    toc_by_book: dict[str, list],
    job_status_by_book: dict[str, dict[str, str]],
    batch_by_book: dict[str, tuple[str, bool]],
) -> list[CoverageEntry]:
    """One `CoverageEntry` per book.

    `books` are row-likes with `.id/.subject/.grade/.status/.source_language/
    .original_filename/.toc_validation`. `toc_by_book` maps book id → its TOC
    rows (row-likes with `.id/.section_title/.page_start/.page_end`).
    `job_status_by_book` maps book id → {toc_entry_id: latest job status}.
    `batch_by_book` maps book id → (batch_id, is_paused). A missing key means
    zero/absent (a book with no TOC yet, or no jobs launched).
    """
    entries: list[CoverageEntry] = []
    for book in books:
        bid = str(book.id)
        batch = batch_by_book.get(bid)
        lessons_total, tally = _lesson_tally(
            toc_by_book.get(bid, []), job_status_by_book.get(bid, {})
        )
        entries.append(
            CoverageEntry(
                grade=book.grade,
                subject=book.subject,
                book_id=bid,
                book_status=book.status,
                source_language=book.source_language,
                original_filename=book.original_filename,
                toc_validation=getattr(book, "toc_validation", None),
                lessons_total=lessons_total,
                **tally,
                batch_id=batch[0] if batch else None,
                paused=bool(batch[1]) if batch else False,
            )
        )
    return entries


def entry_to_dict(entry: CoverageEntry) -> dict[str, Any]:
    return asdict(entry)
