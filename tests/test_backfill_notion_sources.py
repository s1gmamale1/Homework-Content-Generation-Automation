"""Unit tests for `scripts.backfill_notion_sources`'s pure matching decision
(worklog 0144 task 6). No DB, no Notion, no network — `decide_link` takes
plain values and returns a decision + human reason; the script wraps it with
the download/hash/DB-write glue that these tests don't need to exercise."""

from uuid import UUID, uuid4

from scripts.backfill_notion_sources import ExistingBook, decide_link

SUBJECT = "matematika"
SHA = "a" * 64
BOOK_1 = uuid4()
BOOK_2 = uuid4()


def _book(book_id: UUID, subject: str = SUBJECT, sha: str = SHA) -> ExistingBook:
    return ExistingBook(book_id=book_id, subject=subject, content_sha256=sha)


def test_no_match_when_no_books_at_all():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[], linked_book_id=None,
    )
    assert d.action == "no_match"
    assert d.book_id is None


def test_no_match_when_sha_matches_but_subject_differs():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1, subject="fizika")],
        linked_book_id=None,
    )
    assert d.action == "no_match"


def test_no_match_when_subject_matches_but_sha_differs():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1, sha="b" * 64)],
        linked_book_id=None,
    )
    assert d.action == "no_match"


def test_ambiguous_when_two_books_share_subject_and_sha():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1), _book(BOOK_2)],
        linked_book_id=None,
    )
    assert d.action == "ambiguous"
    assert d.book_id is None
    assert str(BOOK_1) in d.reason
    assert str(BOOK_2) in d.reason


def test_would_link_on_unique_match_with_no_existing_link():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1)],
        linked_book_id=None,
    )
    assert d.action == "would_link"
    assert d.book_id == BOOK_1


def test_would_link_when_existing_link_points_at_a_different_book():
    # The (page,block) is currently linked to BOOK_2, but the sha/subject
    # match resolves to BOOK_1 — upsert_link re-points it (matches its own
    # documented ON CONFLICT DO UPDATE semantics), so this is still a write.
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1)],
        linked_book_id=BOOK_2,
    )
    assert d.action == "would_link"
    assert d.book_id == BOOK_1


def test_already_linked_when_link_matches_the_resolved_book():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1)],
        linked_book_id=BOOK_1,
    )
    assert d.action == "already_linked"
    assert d.book_id == BOOK_1
