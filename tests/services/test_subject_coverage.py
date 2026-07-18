"""Unit tests for the pure subject-coverage builder (no DB, no I/O)."""
from dataclasses import dataclass
from typing import Optional

from app.services import subject_coverage as sc


@dataclass
class _Book:
    id: str
    subject: str
    grade: Optional[str]
    status: str
    source_language: str
    original_filename: str
    toc_validation: Optional[str] = None


@dataclass
class _Toc:
    id: str
    section_title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None


def _lesson_rows(n, prefix="t"):
    # plain titles with no keyword hits and no page containment -> classify as "lesson"
    return [_Toc(f"{prefix}{i}", f"Mavzu {i}", page_start=i, page_end=i) for i in range(1, n + 1)]


def test_lessons_total_counts_only_lesson_class_rows():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(3) + [_Toc("x1", "Nazorat ishi", 9, 9), _Toc("x2", "Takrorlash", 10, 10)]
    out = sc.build_coverage([book], {"b1": toc}, {}, {})
    assert len(out) == 1
    # the test/revision rows are excluded by classify_entries
    assert out[0].lessons_total == 3
    assert out[0].done == 0 and out[0].running == 0


def test_job_counts_are_mapped_per_status():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(12)
    # latest status per TOC entry: 5 done, 2 failed, 1 running, 3 pending, 1 cancelled
    statuses = (["done"] * 5 + ["failed"] * 2 + ["running"] + ["pending"] * 3 + ["cancelled"])
    jobs = {"b1": {row.id: st for row, st in zip(toc, statuses)}}
    out = sc.build_coverage([book], {"b1": toc}, jobs, {})
    e = out[0]
    assert (e.done, e.failed, e.running, e.pending, e.cancelled) == (5, 2, 1, 3, 1)
    assert e.lessons_total == 12


def test_jobs_on_non_lesson_rows_do_not_count_toward_done():
    # gate-1 finding: pre-#89 launches were UNFILTERED, so legacy books carry real
    # `done` jobs on test/revision rows. If those counted, a book whose only failed
    # lesson is masked by done non-lesson jobs would falsely report "Finished".
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    lessons = _lesson_rows(3)                       # t1, t2, t3 -> lesson
    extra = [_Toc("x1", "Nazorat ishi", 9, 9), _Toc("x2", "Takrorlash", 10, 10)]
    jobs = {"b1": {
        "t1": "done", "t2": "failed", "t3": "done",  # the real lesson picture
        "x1": "done", "x2": "done",                   # legacy non-lesson jobs
    }}
    e = sc.build_coverage([book], {"b1": lessons + extra}, jobs, {})[0]
    assert e.lessons_total == 3
    assert e.done == 2          # NOT 4 — the test/revision jobs are excluded
    assert e.failed == 1
    # done < lessons_total, so the failed lesson can never be masked as "Finished"
    assert e.done < e.lessons_total


def test_cancelling_is_folded_into_running():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    toc = _lesson_rows(2)
    jobs = {"b1": {"t1": "cancelling", "t2": "running"}}
    e = sc.build_coverage([book], {"b1": toc}, jobs, {})[0]
    assert e.running == 2


def test_missing_toc_and_missing_jobs_default_to_zero():
    book = _Book("b1", "musiqa", "5", "toc_extracting", "uz", "mus5.pdf")
    out = sc.build_coverage([book], {}, {}, {})
    e = out[0]
    assert e.lessons_total == 0
    assert (e.done, e.failed, e.running, e.pending, e.cancelled) == (0, 0, 0, 0, 0)
    assert e.book_status == "toc_extracting"


def test_batch_link_and_paused_flag_are_threaded():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf")
    out = sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {"b1": ("batch-7", True)})
    assert out[0].batch_id == "batch-7" and out[0].paused is True

    out2 = sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {})
    assert out2[0].batch_id is None and out2[0].paused is False


def test_null_grade_is_preserved_not_defaulted():
    book = _Book("b1", "biology", None, "toc_ready", "uz", "bio.pdf")
    out = sc.build_coverage([book], {"b1": _lesson_rows(1)}, {}, {})
    assert out[0].grade is None


def test_multiple_books_for_same_grade_subject_are_both_returned():
    # a grade+subject can legitimately have a uz AND a ru textbook — never collapse
    a = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9-uz.pdf")
    b = _Book("b2", "biology", "9", "toc_ready", "ru", "bio9-ru.pdf")
    out = sc.build_coverage([a, b], {"b1": _lesson_rows(4), "b2": _lesson_rows(6)}, {}, {})
    assert {e.book_id for e in out} == {"b1", "b2"}
    assert {e.lessons_total for e in out} == {4, 6}


def test_entry_to_dict_is_json_shaped():
    book = _Book("b1", "biology", "9", "toc_ready", "uz", "bio9.pdf", toc_validation="verified")
    d = sc.entry_to_dict(sc.build_coverage([book], {"b1": _lesson_rows(2)}, {}, {})[0])
    assert d["subject"] == "biology" and d["grade"] == "9"
    assert d["lessons_total"] == 2 and d["toc_validation"] == "verified"
    import json
    json.dumps(d)
