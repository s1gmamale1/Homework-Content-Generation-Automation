"""Unit tests for `scripts.backfill_notion_sources`'s pure matching decision
and preflight/refusal logic (worklog 0144 task 6 + acceptance fixes). No DB,
no Notion, no network — `decide_link` and the preflight helpers take plain
values; the script wraps them with the download/hash/DB-write glue that
these tests don't need to exercise."""

import asyncio
from uuid import UUID, uuid4

import pytest

from scripts.backfill_notion_sources import (
    ACTIONS,
    DATABASE_URL_ERROR,
    MIGRATION_ERROR,
    NOTION_KEY_ERROR,
    ExistingBook,
    PreflightError,
    assert_book_notion_sources_exists,
    decide_link,
    format_applied,
    format_dry_run,
    format_summary,
    preflight_database_url,
    preflight_notion_api_key,
)

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


def test_would_repoint_when_existing_link_points_at_a_different_book():
    # The (page,block) is currently linked to BOOK_2, but the sha/subject
    # match resolves to BOOK_1 — upsert_link re-points it (its documented
    # ON CONFLICT DO UPDATE semantics), so --apply still writes. But the
    # dry-run report must SURFACE the re-point distinctly (review MEDIUM):
    # an operator eyeballing "would link" lines before --apply has no way
    # to spot a link being MOVED off a book unless it's its own action,
    # with a reason naming BOTH books.
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1)],
        linked_book_id=BOOK_2,
    )
    assert d.action == "would_repoint"
    assert d.book_id == BOOK_1
    assert d.reason == f"was book {BOOK_2} -> now book {BOOK_1}"


def test_already_linked_when_link_matches_the_resolved_book():
    d = decide_link(
        candidate_sha256=SHA, candidate_subject=SUBJECT,
        existing_books=[_book(BOOK_1)],
        linked_book_id=BOOK_1,
    )
    assert d.action == "already_linked"
    assert d.book_id == BOOK_1


# ─── report/summary formatting (pure — backs the dry-run/apply output) ───


def _counts(**overrides) -> dict:
    base = {a: 0 for a in ACTIONS}
    base.update(overrides)
    return base


def test_actions_tuple_covers_every_decide_link_action():
    # The counts dict in run() is built from ACTIONS — a decide_link action
    # missing from it would KeyError mid-run after downloads. Pin the set.
    assert ACTIONS == ("would_link", "would_repoint", "already_linked", "no_match", "ambiguous")


def test_format_summary_splits_repoints_out():
    line = format_summary(_counts(would_link=3, would_repoint=2, already_linked=1, no_match=4))
    assert line == (
        "summary: would_link=3 would_repoint=2 already_linked=1 no_match=4 ambiguous=0"
    )


def test_format_dry_run_names_both_write_kinds():
    assert format_dry_run(_counts(would_link=3, would_repoint=2)) == (
        "DRY RUN — would link 3, would re-point 2. Pass --apply to write."
    )


def test_format_applied_reports_repoints_separately():
    assert format_applied(5, 2) == "applied 5 link(s), of which 2 re-pointed."
    assert format_applied(3, 0) == "applied 3 link(s), of which 0 re-pointed."


# ─── preflight/refusal logic (acceptance fixes) ───
# The operator-facing behavior these back: a missing DATABASE_URL / missing
# NOTION_API_KEY / un-migrated target DB must each produce ONE clear error
# line + exit code 2 — never a raw traceback (and, for the migration check,
# never AFTER PDFs have already been downloaded).


def test_preflight_database_url_missing_raises_clear_error():
    with pytest.raises(PreflightError) as exc:
        preflight_database_url({})
    assert str(exc.value) == DATABASE_URL_ERROR
    assert "must be set explicitly" in str(exc.value)


def test_preflight_database_url_empty_string_is_missing():
    with pytest.raises(PreflightError):
        preflight_database_url({"DATABASE_URL": ""})


def test_preflight_database_url_present_returns_it():
    url = "postgresql+asyncpg://edu:edu@localhost:5433/edu_homework"
    assert preflight_database_url({"DATABASE_URL": url}) == url


def test_preflight_notion_api_key_missing_raises_clear_error():
    with pytest.raises(PreflightError) as exc:
        preflight_notion_api_key("")
    assert str(exc.value) == NOTION_KEY_ERROR


def test_preflight_notion_api_key_present_returns_it():
    assert preflight_notion_api_key("ntn_abc") == "ntn_abc"


class _StubResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _StubSession:
    """Fake AsyncSession: `execute` resolves to a stub whose .scalar() is the
    to_regclass('book_notion_sources') answer — the table's regclass when it
    exists, None when the target DB was never migrated to 0048."""

    def __init__(self, regclass_value):
        self._regclass_value = regclass_value

    async def execute(self, *_args, **_kwargs):
        return _StubResult(self._regclass_value)


def test_migration_preflight_raises_when_table_absent():
    with pytest.raises(PreflightError) as exc:
        asyncio.run(assert_book_notion_sources_exists(_StubSession(None)))
    assert str(exc.value) == MIGRATION_ERROR
    assert "migration 0048" in str(exc.value)
    assert "alembic upgrade head" in str(exc.value)


def test_migration_preflight_passes_when_table_exists():
    # Must simply not raise.
    asyncio.run(assert_book_notion_sources_exists(_StubSession("book_notion_sources")))
