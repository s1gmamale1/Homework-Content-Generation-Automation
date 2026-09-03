"""Distinct lessons that share a title must not collapse onto one Notion page.

The live failure (2026-08-05): 184 jobs stamped `notion_archived_at`, but only
135 distinct Homework pages exist. Uzbek/Russian maths textbooks reuse rubric
headings as section titles — `Вспомните` appears 10 times in one grade,
`Подумайте. Проблемное задание` 13 times — and `section_number` is NULL for
exactly those rows, so `_lesson_title` returns the bare, repeated title.
`find_or_create` matches on the normalized title, so every one of them resolves
to the SAME lesson page; the first job populates it and the rest hit
`page_has_content` and return without writing. 49 homeworks were never archived,
and every one of them reports success.

These tests drive the real `_push_to_notion` with a fake Notion client rather
than asserting on a helper, because the bug lives in how the real function
composes `find_or_create` + `page_has_content`.
"""
from types import SimpleNamespace

import app.services.notion_archive as na


class FakeNotion:
    """Records every page created and every write, and models Notion's own
    'a page keeps its content' behaviour that turns a collision into a skip."""

    def __init__(self):
        self.pages: dict[str, dict] = {}      # id -> {"title", "parent"}
        self.content: dict[str, list] = {}    # id -> appended blocks
        self._n = 0

    # -- the subset of NotionClientWrapper that _push_to_notion touches --
    def get_child_pages(self, parent_id):
        return [{"id": pid, "title": p["title"]}
                for pid, p in self.pages.items() if p["parent"] == parent_id]

    def create_page(self, parent_id, title, children=None):
        self._n += 1
        pid = f"pg{self._n}"
        self.pages[pid] = {"title": title, "parent": parent_id}
        return {"id": pid}

    def page_has_content(self, page_id):
        return bool(self.content.get(page_id))

    def page_is_live(self, page_id):
        return True   # fake pages are never in Notion trash

    def get_page_parent(self, page_id):
        page = self.pages.get(page_id)
        return page["parent"] if page else None

    def append_block_children(self, block_id, children):
        self.content.setdefault(block_id, []).extend(children)

    def clear_content_blocks(self, page_id):
        self.content.pop(page_id, None)

    def upload_bytes(self, data, file_name, content_type):
        return "file-upload-id"

    # -- helpers for assertions --
    def lesson_pages_under(self, container_title="Platform Homeworks"):
        containers = [pid for pid, p in self.pages.items()
                      if p["title"] == container_title]
        return [p["title"] for pid, p in self.pages.items()
                if p["parent"] in containers]

    def written_leaf_count(self):
        return sum(1 for pid, blocks in self.content.items() if blocks)


_PHASES = {"case-based-preview": "# Preview\n\nBody text."}


def _push(client, title):
    _, homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subject1",
        lesson_title=title,
        phase_md=_PHASES,
    )
    return homework_id


def test_two_lessons_sharing_a_title_get_two_pages():
    """THE BUG, driven through the REAL decision function.

    Two different lessons from one book, both titled `Вспомните` with a NULL
    section_number — the live shape. The titles are NOT hardcoded: they come
    from `resolve_lesson_title`, the function production actually calls.
    """
    from uuid import UUID
    c = FakeNotion()
    a = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    b = UUID("bbbbbbbb-0000-0000-0000-000000000002")
    sibs = [(None, "Вспомните", "", 12, a), (None, "Вспомните", "", 47, b)]
    t1 = na.resolve_lesson_title(
        SimpleNamespace(section_number=None, section_title="Вспомните", page_start=12, id=a), sibs)
    t2 = na.resolve_lesson_title(
        SimpleNamespace(section_number=None, section_title="Вспомните", page_start=47, id=b), sibs)

    first, second = _push(c, t1), _push(c, t2)

    assert first != second, "both lessons resolved to the SAME Homework page"
    assert sorted(c.lesson_pages_under()) == ["Вспомните · p.12", "Вспомните · p.47"]
    # Both must actually receive content — a skipped write is the real damage.
    assert c.written_leaf_count() == 2


def test_identical_titles_still_collide_without_a_disambiguator():
    """Guard-rail: this documents the collapse that the suffix exists to prevent.

    If this ever stops holding, `find_or_create` changed and the suffix logic
    in `archive_job` may no longer be what is keeping lessons apart.
    """
    c = FakeNotion()

    first = _push(c, "Вспомните")
    second = _push(c, "Вспомните")

    assert first == second                      # same page by title
    assert c.written_leaf_count() == 1          # second write skipped


def test_a_known_homework_page_is_reused_without_touching_titles():
    """Identity from the DB beats identity from the title.

    A section that already owns a page must reuse it by id — this is what stops
    the 9 legitimate owner pages (whose titles ARE ambiguous) from being
    re-keyed onto fresh suffixed pages and orphaning their content.
    """
    c = FakeNotion()
    known = _push(c, "Проценты")          # first archive creates the tree
    c.content.clear()                     # pretend the leaves are empty again

    _, again = na._push_to_notion(
        client=c,
        subject_page_id="subject1",
        lesson_title="COMPLETELY DIFFERENT TITLE",
        phase_md=_PHASES,
        homework_page_id=known,
    )

    assert again == known
    # No new lesson page was invented despite the title not matching anything.
    assert c.lesson_pages_under() == ["Проценты"]


# --- the DETECTION itself -----------------------------------------------------
# The first version of this fix tested only `_lesson_title(ambiguous=True)` — a
# hardcoded-correct input. The whole detection could be deleted with a green
# suite. These drive `resolve_lesson_title` with realistic sibling rows, which
# is what `archive_job` actually passes.

from uuid import UUID

def _row(sn, st, page_start, tid, ct=""):
    return (sn, st, ct, page_start, UUID(tid))


def _sec(sn, st, page_start, tid):
    return SimpleNamespace(section_number=sn, section_title=st,
                           page_start=page_start, id=UUID(tid))


_A = "aaaaaaaa-0000-0000-0000-000000000001"
_B = "bbbbbbbb-0000-0000-0000-000000000002"
_C = "cccccccc-0000-0000-0000-000000000003"


def test_unique_title_is_left_alone():
    """The 135 currently-correct pages depend on this."""
    sec = _sec(None, "Проценты", 30, _A)
    siblings = [_row(None, "Проценты", 30, _A), _row(None, "Объём", 44, _B)]
    assert na.resolve_lesson_title(sec, siblings) == "Проценты"


def test_repeated_title_is_disambiguated_by_page():
    sec = _sec(None, "Вспомните", 64, _A)
    siblings = [_row(None, "Вспомните", 64, _A), _row(None, "Вспомните", 90, _B)]
    assert na.resolve_lesson_title(sec, siblings) == "Вспомните · p.64"


def test_same_title_AND_same_page_falls_back_to_the_row_id():
    """The live Part I / Part II case.

    Both books share a Notion container and both restart pagination, so
    `Вспомните` is at page 2 in each. page_start does NOT separate them, and
    order_index does not either (both are 1) — so the suffix must escalate.
    """
    sec = _sec(None, "Вспомните", 2, _A)
    siblings = [_row(None, "Вспомните", 2, _A), _row(None, "Вспомните", 2, _B)]

    got = na.resolve_lesson_title(sec, siblings)

    assert got == "Вспомните · p.2 · aaaaaaaa"
    other = na.resolve_lesson_title(_sec(None, "Вспомните", 2, _B), siblings)
    assert got != other, "the two Part I/II lessons still collide"


def test_case_and_whitespace_variants_count_as_the_same_title():
    """`ПОВТОРЕНИЕ 2` and `Повторение 2` collided live — find_or_create folds case."""
    sec = _sec(None, "ПОВТОРЕНИЕ 2", 88, _A)
    siblings = [_row(None, "ПОВТОРЕНИЕ 2", 88, _A), _row(None, "  повторение 2 ", 91, _B)]
    assert na.resolve_lesson_title(sec, siblings) == "ПОВТОРЕНИЕ 2 · p.88"


def test_sibling_rows_falling_back_to_chapter_title_still_match():
    """A sibling with an empty section_title is titled from chapter_title; if the
    two sides disagreed, a row would fail to match itself and the count would
    read 0 — silently suppressing the suffix."""
    sec = _sec(None, "Повторение", 10, _A)
    siblings = [_row(None, "Повторение", 10, _A), _row(None, "", 20, _B, ct="Повторение")]
    assert na.resolve_lesson_title(sec, siblings).startswith("Повторение · p.10")


def test_a_lesson_alone_in_its_container_is_not_suffixed():
    sec = _sec("1.1", "Burchaklar", 5, _A)
    assert na.resolve_lesson_title(sec, [_row("1.1", "Burchaklar", 5, _A)]) == "1.1 Burchaklar"


# --- archive_job WIRING -------------------------------------------------------
# The decision function being correct is worth nothing if archive_job does not
# call it. Reverting either wiring point left the suite fully green twice, so
# these assert on the kwargs that actually reach the Notion push.

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _wire_job(monkeypatch, *, section_page_id=None):
    job = SimpleNamespace(
        id=UUID(_C), book_id=UUID(_B), toc_entry_id=UUID(_A),
        subject="matematika", output_language="ru",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        notion_archived_at=None,
    )
    section = SimpleNamespace(
        id=UUID(_A), section_number=None, section_title="Вспомните",
        chapter_title="", page_start=2, order_index=1,
        notion_homework_page_id=section_page_id, notion_archived_job_id=None,
        notion_lesson_page_id=None,
    )
    book = SimpleNamespace(id=job.book_id, grade="5", original_filename="m5.pdf")
    monkeypatch.setattr(na.settings, "notion_enabled", True)
    monkeypatch.setattr(na.settings, "notion_api_key", "ntn_x")
    monkeypatch.setattr(na.settings, "notion_subject_pages", {"ru:matematika|5": "subj"})
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(na, "SessionLocal", MagicMock(return_value=session))
    # A REALISTIC sibling list: this section's own row plus a Part-II row that
    # shares both the title AND the page number. `[]` is not a state the DB can
    # produce — a section always appears in its own container.
    siblings = [(None, "Вспомните", "", 2, UUID(_A)), (None, "Вспомните", "", 2, UUID(_B))]
    phase = SimpleNamespace(phase_name="case-based-preview", status="done", output_md="# CBP")
    return job, section, book, siblings, phase


@pytest.mark.asyncio
async def test_archive_job_sends_the_DISAMBIGUATED_title_to_notion(monkeypatch):
    """Reverting this wiring to the pre-fix `_lesson_title(...)` must go RED."""
    job, section, book, siblings, phase = _wire_job(monkeypatch)
    push = AsyncMock(return_value=(None, "hw1"))
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=siblings)), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()), \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na, "_push_with_retry", push):
        await na.archive_job(job.id)

    sent = push.await_args.kwargs["lesson_title"]
    # Title AND page collide with the Part-II row, so level 3 must engage.
    assert sent == "Вспомните · p.2 · aaaaaaaa", sent
    assert sent != "Вспомните", "archive_job is not using resolve_lesson_title"


@pytest.mark.asyncio
async def test_archive_job_forwards_an_owned_page_id(monkeypatch):
    """The 200 already-archived sections whose title CHANGES under this fix are
    protected by this one argument. Dropping it re-keys them onto fresh pages
    and orphans their content."""
    job, section, book, siblings, phase = _wire_job(monkeypatch, section_page_id="existing-hw")
    push = AsyncMock(return_value=(None, "existing-hw"))
    with patch.object(na.jobs_repo, "get", AsyncMock(return_value=job)), \
         patch.object(na.books_repo, "get", AsyncMock(return_value=book)), \
         patch.object(na.toc_repo, "get", AsyncMock(return_value=section)), \
         patch.object(na.toc_repo, "titles_for_subject_grade", AsyncMock(return_value=siblings)), \
         patch.object(na.toc_repo, "set_notion_homework_page_id", AsyncMock()), \
         patch.object(na.toc_repo, "set_notion_archived_job", AsyncMock()), \
         patch.object(na.jobs_repo, "set_notion_archived", AsyncMock()), \
         patch.object(na.phase_repo, "list_for_job", AsyncMock(return_value=[phase])), \
         patch.object(na, "_push_with_retry", push):
        await na.archive_job(job.id)

    assert push.await_args.kwargs["homework_page_id"] == "existing-hw"


def test_ambiguous_row_with_NO_page_start_escalates_to_the_row_id():
    """The `page_start IS NULL` arm. No live row hits it today, but a mutation
    to it was invisible — and a TOC re-extract can produce page-less rows."""
    sec = _sec(None, "Вспомните", None, _A)
    sibs = [_row(None, "Вспомните", None, _A), _row(None, "Вспомните", None, _B)]

    got = na.resolve_lesson_title(sec, sibs)

    assert got == "Вспомните · aaaaaaaa"
    assert got != na.resolve_lesson_title(_sec(None, "Вспомните", None, _B), sibs)


def test_TARGET_titled_from_chapter_title_matches_its_siblings():
    """Symmetric half of the sibling fallback: when the TARGET's section_title is
    empty it must also fall back to chapter_title, or it fails to match its own
    row and the suffix is silently suppressed."""
    sec = SimpleNamespace(section_number=None, section_title="",
                          chapter_title="Повторение", page_start=10, id=UUID(_A))
    sibs = [_row(None, "", 10, _A, ct="Повторение"), _row(None, "Повторение", 20, _B)]

    assert na.resolve_lesson_title(sec, sibs) == "Повторение · p.10"


def test_a_legacy_container_page_is_never_reused_or_touched():
    """The 2026-08-31 cutover guard: a stored pointer into a frozen legacy
    'Generated Homeworks' tree is ignored — its pages get no write, no clear —
    and the homework files fresh under the current container instead."""
    c = FakeNotion()
    legacy_container = c.create_page("subject1", "Generated Homeworks")["id"]
    legacy_lesson = c.create_page(legacy_container, "Проценты")["id"]
    legacy_homework = c.create_page(legacy_lesson, "Homework")["id"]
    c.content[legacy_homework] = [{"type": "paragraph"}]      # frozen content
    frozen = list(c.content[legacy_homework])

    _, homework_id = na._push_to_notion(
        client=c,
        subject_page_id="subject1",
        lesson_title="Проценты",
        phase_md=_PHASES,
        homework_page_id=legacy_homework,
        lesson_page_id=legacy_lesson,
    )

    assert homework_id != legacy_homework
    assert c.content[legacy_homework] == frozen                # untouched
    assert c.lesson_pages_under("Platform Homeworks") == ["Проценты"]
    assert c.lesson_pages_under("Generated Homeworks") == ["Проценты"]  # still there
