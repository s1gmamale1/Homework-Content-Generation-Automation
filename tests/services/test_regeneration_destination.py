"""The read-only Notion destination preflight: decide before anything is spent.

No database, no network, no credential, no model. Notion is `FakeNotion` — the
same in-memory model `test_notion_versioned_homework.py` and the publisher
harness already use — subclassed here into a READ-ONLY client whose entire write
surface is a tripwire. That subclass is the point of the file: a preflight that
creates a page has already made the decision it was supposed to be asking the
operator about.

What this file is holding down:

* **the preflight writes NOTHING.** `create_page`, `append_block_children`,
  `delete_block`, `clear_content_blocks` and `upload_bytes` all raise, and so do
  both `find_or_create`s — the two functions whose whole job is to create.
* **one client, one thread hop, one read per page.** The client is built inside
  the worker thread (its constructor opens an HTTP client), every remote read is
  cached by id for the whole call, and nothing touches the event loop.
* **normalization parity with publication.** Review imports
  `page_creator._normalize` rather than re-deriving it, so the trailing `(N)`
  Notion appends folds identically on both sides. The measured duplicate shape
  — `7 Photosynthesis` beside `7 Photosynthesis (2)` — is therefore AMBIGUOUS
  here, not a silent adoption.
* **fail closed.** No subject mapping, two containers, an override that is not
  one of the offered candidates, or an existing `Homework V{n}` all block, and a
  partial scan RAISES rather than returning a result that reads like a decision.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx
import pytest
from notion_client.errors import APIErrorCode, APIResponseError

import app.services.regeneration_destination as dest
from app.services.notion.page_creator import _normalize
from tests.services.test_notion_versioned_homework import FakeNotion

_SUBJECT_UZ = "subject-uz-biology-7"
_SUBJECT_RU = "subject-ru-biology-7"
_CONTAINER = "Generated Homeworks"
_TITLE = "7 Photosynthesis"
_MAPPING = {"biology|7": _SUBJECT_UZ, "ru:biology|7": _SUBJECT_RU}
#: Shaped like a real Notion integration token and never used against Notion.
_USABLE_KEY = "secret_pytest_not_a_real_notion_token"

_WRITES = (
    "create_page", "append_block_children", "delete_block",
    "clear_content_blocks", "upload_bytes",
)


class _ReadOnlyNotion(FakeNotion):
    """`FakeNotion` with its whole write surface wired to explode.

    Subclassed rather than re-implemented: the read semantics that matter here
    (a sub-page is a `child_page` block in its parent's block list) are the ones
    the versioned-homework and publisher suites already prove against the real
    API's shape, and a second model of Notion would drift from them.
    """

    def __init__(self) -> None:
        super().__init__()
        self.threads: list[threading.Thread] = []

    def seed(self, parent_id: str, title: str, page_id: str) -> str:
        """`add_page` with a caller-chosen id, so a test can name the page a
        stored pointer points at."""
        self.titles[page_id] = title
        self.parents[page_id] = parent_id
        self.blocks.setdefault(page_id, [])
        self.blocks.setdefault(parent_id, []).append(
            {"id": page_id, "type": "child_page", "child_page": {"title": title}}
        )
        return page_id

    # every read records the thread it ran on
    def get_block_children(self, block_id: str) -> list[dict]:
        self.threads.append(threading.current_thread())
        return super().get_block_children(block_id)

    def get_child_pages(self, parent_id: str) -> list[dict]:
        self.threads.append(threading.current_thread())
        return super().get_child_pages(parent_id)

    def get_page_parent(self, page_id: str) -> Optional[str]:
        self.threads.append(threading.current_thread())
        return self.parents.get(page_id)

    def get_page_title(self, page_id: str) -> str:
        self.threads.append(threading.current_thread())
        return self.titles.get(page_id, "")


def _forbid_write(name: str):
    def _boom(*_a, **_kw):
        raise AssertionError(
            f"the destination preflight must never call {name} — it decides, "
            "it does not build"
        )
    return _boom


for _method in _WRITES:
    setattr(_ReadOnlyNotion, _method, _forbid_write(f"client.{_method}"))


@dataclass
class _Resolver:
    """Binds a fake client factory and a default requested version to the real
    module-level entry point, which is what production calls."""

    notion: _ReadOnlyNotion
    requested_version: int = 3
    factory_calls: int = 0
    thread_hops: list[str] = field(default_factory=list)
    factory_threads: list[threading.Thread] = field(default_factory=list)

    def _factory(self) -> _ReadOnlyNotion:
        self.factory_calls += 1
        self.factory_threads.append(threading.current_thread())
        return self.notion

    async def resolve(self, *, sources, overrides=(), requested_version=None,
                      **kwargs) -> "dest.DestinationPreflight":
        return await dest.resolve_destinations(
            sources=tuple(sources),
            requested_version=(
                self.requested_version if requested_version is None
                else requested_version
            ),
            overrides=tuple(overrides),
            client_factory=self._factory,
            **kwargs,
        )

    def off_loop(self) -> bool:
        main = threading.main_thread()
        return (
            bool(self.notion.threads)
            and all(t is not main for t in self.notion.threads)
            and bool(self.factory_threads)
            and all(t is not main for t in self.factory_threads)
        )


@pytest.fixture
def notion() -> _ReadOnlyNotion:
    fake = _ReadOnlyNotion()
    fake.titles[_SUBJECT_UZ] = "Biology 7"
    fake.blocks[_SUBJECT_UZ] = []
    fake.titles[_SUBJECT_RU] = "Biologiya 7 ru"
    fake.blocks[_SUBJECT_RU] = []
    return fake


@pytest.fixture
def resolver(notion, monkeypatch) -> _Resolver:
    monkeypatch.setattr(dest.settings, "notion_enabled", True)
    monkeypatch.setattr(dest.settings, "notion_api_key", _USABLE_KEY)
    monkeypatch.setattr(dest.settings, "notion_subject_pages", dict(_MAPPING))
    # The two functions whose entire purpose is to CREATE. A preflight that
    # reached either has already changed the thing it was asked to describe.
    from app.services import notion_archive
    from app.services.notion import page_creator

    monkeypatch.setattr(
        notion_archive, "find_or_create",
        _forbid_write("notion_archive.find_or_create"))
    monkeypatch.setattr(
        page_creator, "find_or_create",
        _forbid_write("page_creator.find_or_create"))
    return _Resolver(notion=notion)


def source(
    *,
    pointer: Optional[str] = None,
    language: str = "uz",
    title: str = _TITLE,
    toc_entry_id: Optional[uuid.UUID] = None,
    subject: str = "biology",
    grade: Optional[str] = "7",
    section_number: Optional[str] = "7",
    section_title: str = "Photosynthesis",
    book_filename: str = "biology7.pdf",
    homework_pointer: Optional[str] = None,
    homework_lineage_verified: bool = False,
    lineage_previously_published: bool = False,
) -> "dest.DestinationSource":
    return dest.DestinationSource(
        toc_entry_id=toc_entry_id or uuid.uuid4(),
        output_language=language,
        source_job_id=uuid.uuid4(),
        subject=subject,
        grade=grade,
        book_filename=book_filename,
        section_number=section_number,
        section_title=section_title,
        chapter_title="Chapter 2",
        page_start=41,
        notion_lesson_page_id=pointer,
        lesson_title=title,
        notion_homework_page_id=homework_pointer,
        notion_homework_lineage_verified=homework_lineage_verified,
        lineage_previously_published=lineage_previously_published,
    )


def _container(notion: _ReadOnlyNotion, parent: str = _SUBJECT_UZ,
               page_id: str = "container-uz") -> str:
    return notion.seed(parent, _CONTAINER, page_id)


def _one(result: "dest.DestinationPreflight") -> "dest.DestinationResolution":
    assert len(result.resolutions) == 1
    return result.resolutions[0]


# ═══════════════ the decision: reuse, create, ambiguous, blocked ══════════


async def test_valid_stored_pointer_is_reused(resolver, notion):
    """The ordinary already-archived lesson. The pointer short-circuits the
    title lookup — which is what keeps a lesson whose disambiguating suffix has
    since changed on the page it already has."""
    container = _container(notion)
    notion.seed(container, "7 Photosynthesis · p.41", "lesson-1")

    result = await resolver.resolve(sources=[source(pointer="lesson-1")],
                                    overrides=())

    assert _one(result).lesson_policy == "reuse"
    assert _one(result).lesson_page_id == "lesson-1"
    assert _one(result).status == "reuse"
    assert _one(result).container_policy == "reuse"
    assert _one(result).container_page_id == container
    assert result.ok is True


async def test_verified_v1_homework_parent_is_authoritative_without_a_mapping(
    resolver, notion, monkeypatch
):
    """Proven archive lineage is stronger than mutable mapping or title data."""
    legacy_subject = notion.seed("workspace", "Legacy biology", "legacy-subject")
    container = notion.seed(legacy_subject, _CONTAINER, "legacy-container")
    legacy_lesson = notion.seed(container, "7 Photosynthesis", "legacy-lesson")
    legacy_homework = notion.seed(legacy_lesson, "Homework", "legacy-homework")
    matching = source(
        pointer=None,
        homework_pointer=legacy_homework,
        title="7 Photosynthesis · p.41 · deadbeef",
        homework_lineage_verified=True,
        lineage_previously_published=True,
    )
    monkeypatch.setattr(dest.settings, "notion_subject_pages", {})
    result = await resolver.resolve(sources=[matching], overrides=())

    adopted = _one(result)
    assert adopted.status == "reuse"
    assert adopted.lesson_policy == "reuse"
    assert adopted.lesson_page_id == legacy_lesson
    assert adopted.container_page_id == container
    assert result.ok is True


async def test_a_published_lineage_without_language_proof_blocks_instead_of_creating(
    resolver, notion
):
    container = _container(notion)
    legacy_lesson = notion.seed(container, _TITLE, "legacy-lesson")
    legacy_homework = notion.seed(legacy_lesson, "Homework", "legacy-homework")
    _container(notion, parent=_SUBJECT_RU, page_id="container-ru")

    result = await resolver.resolve(sources=[source(
        pointer=None,
        homework_pointer=legacy_homework,
        language="ru",
        homework_lineage_verified=False,
        lineage_previously_published=True,
    )])

    blocked = _one(result)
    assert blocked.status == "blocked"
    assert "published" in (blocked.reason or "").lower()
    assert blocked.lesson_page_id is None
    assert result.ok is False


async def test_an_operator_can_select_a_candidate_for_an_unproven_published_lineage(
    resolver, notion
):
    container = _container(notion)
    lesson = notion.seed(container, _TITLE, "candidate-lesson")
    item = source(
        pointer=None,
        homework_pointer="pointer-owned-by-another-language",
        homework_lineage_verified=False,
        lineage_previously_published=True,
    )

    review = await resolver.resolve(sources=[item])
    assert _one(review).status == "ambiguous"
    assert [candidate.page_id for candidate in _one(review).candidates] == [lesson]

    approved = await resolver.resolve(
        sources=[item],
        overrides=[dest.DestinationOverride(
            toc_entry_id=item.toc_entry_id,
            output_language=item.output_language,
            notion_lesson_page_id=lesson,
        )],
    )
    assert _one(approved).status == "reuse"
    assert _one(approved).lesson_page_id == lesson
    assert approved.ok is True


async def test_a_deleted_verified_v1_pointer_blocks_instead_of_title_matching(
    resolver, notion, monkeypatch
):
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")
    real_get_parent = notion.get_page_parent

    def missing_homework(page_id: str) -> Optional[str]:
        if page_id == "deleted-homework":
            raise APIResponseError(
                code=APIErrorCode.ObjectNotFound,
                status=404,
                message="Could not find page",
                headers=httpx.Headers(),
                raw_body_text="",
            )
        return real_get_parent(page_id)

    monkeypatch.setattr(notion, "get_page_parent", missing_homework)

    result = await resolver.resolve(
        sources=[source(
            pointer=None,
            homework_pointer="deleted-homework",
            homework_lineage_verified=True,
            lineage_previously_published=True,
        )],
        overrides=(),
    )

    assert _one(result).status == "blocked"
    assert "stored v1 homework" in (_one(result).reason or "").lower()
    assert _one(result).lesson_page_id is None
    assert result.ok is False


async def test_a_verified_lineage_with_an_existing_version_blocks_without_mapping(
    resolver, notion, monkeypatch
):
    container = notion.seed("legacy-subject", _CONTAINER, "legacy-container")
    lesson = notion.seed(container, "Old title", "legacy-lesson")
    homework = notion.seed(lesson, "Homework", "legacy-homework")
    notion.seed(lesson, "Homework V3", "existing-v3")
    monkeypatch.setattr(dest.settings, "notion_subject_pages", {})

    result = await resolver.resolve(sources=[source(
        homework_pointer=homework,
        homework_lineage_verified=True,
        lineage_previously_published=True,
    )])

    blocked = _one(result)
    assert blocked.status == "blocked"
    assert "Homework V3 already exists" in (blocked.reason or "")
    assert blocked.container_page_id == container
    assert blocked.lesson_page_id == lesson
    assert result.ok is False


async def test_two_safe_matches_block_until_operator_selects_one(resolver, notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "a")
    notion.seed(container, _TITLE, "b")

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "ambiguous"
    assert [c.page_id for c in _one(result).candidates] == ["a", "b"]
    assert result.ok is False
    assert _one(result).lesson_policy is None
    assert _one(result).lesson_page_id is None


async def test_the_measured_duplicate_shape_is_ambiguous_not_adopted(resolver,
                                                                     notion):
    """The shape actually measured in the live tree: Notion appended `(2)` to a
    second page with the same name. `_normalize` folds that suffix, so BOTH are
    equally good matches — and picking either silently is how a revision lands
    on the wrong lesson."""
    container = _container(notion)
    notion.seed(container, "7 Photosynthesis", "a")
    notion.seed(container, "7 Photosynthesis (2)", "b")

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _normalize("7 Photosynthesis (2)") == _normalize("7 Photosynthesis")
    assert _one(result).status == "ambiguous"
    assert [c.page_id for c in _one(result).candidates] == ["a", "b"]
    assert [c.title for c in _one(result).candidates] == [
        "7 Photosynthesis", "7 Photosynthesis (2)"]
    assert result.ok is False


async def test_one_normalized_match_is_adopted(resolver, notion):
    """A single deduped page is not ambiguous — it is the lesson, wearing the
    suffix Notion gave it. Adopting it is what keeps V2 a SIBLING of V1."""
    container = _container(notion)
    notion.seed(container, "7 Photosynthesis (2)", "only")

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "reuse"
    assert _one(result).lesson_policy == "reuse"
    assert _one(result).lesson_page_id == "only"
    assert result.ok is True


async def test_no_match_creates_a_new_lesson_topic(resolver, notion):
    container = _container(notion)
    notion.seed(container, "6 Respiration", "other")

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "create"
    assert _one(result).lesson_policy == "create"
    assert _one(result).lesson_page_id is None
    assert _one(result).container_policy == "reuse"
    assert _one(result).container_page_id == container
    assert _one(result).candidates == ()
    assert result.ok is True


async def test_a_pointer_into_another_language_container_is_not_membership(
    resolver, notion
):
    """`toc_entries.notion_lesson_page_id` is ONE language-blind column. A `ru`
    lineage that trusted it would file under the `uz` Lesson Topic. Membership
    of THIS language's container is the proof, so the foreign pointer falls
    through to title matching instead of routing anything."""
    uz_container = _container(notion)
    uz_lesson = notion.seed(uz_container, _TITLE, "uz-lesson")
    ru_container = _container(notion, parent=_SUBJECT_RU, page_id="container-ru")
    notion.seed(ru_container, _TITLE, "ru-lesson")

    result = await resolver.resolve(
        sources=[source(pointer=uz_lesson, language="ru")], overrides=())

    assert _one(result).lesson_page_id == "ru-lesson"
    assert _one(result).container_page_id == ru_container
    assert _one(result).status == "reuse"


async def test_a_blank_stored_pointer_is_treated_as_absent(resolver, notion):
    """Task 1's check constraint only tests `IS NOT NULL`, so `''` reaches the
    service from the database. A legacy blank is not an error — it is simply no
    pointer."""
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    result = await resolver.resolve(sources=[source(pointer="   ")], overrides=())

    assert _one(result).status == "reuse"
    assert _one(result).lesson_page_id == "lesson-1"


async def test_a_pointer_that_names_no_page_at_all_falls_through(resolver, notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    result = await resolver.resolve(sources=[source(pointer="deleted-page")],
                                    overrides=())

    assert _one(result).lesson_page_id == "lesson-1"


# ═══════════════════════════ the operator's override ═════════════════════


async def test_an_override_selects_one_of_the_offered_candidates(resolver, notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "a")
    notion.seed(container, _TITLE, "b")
    src = source(pointer=None)

    result = await resolver.resolve(
        sources=[src],
        overrides=[dest.DestinationOverride(
            toc_entry_id=src.toc_entry_id, output_language=src.output_language,
            notion_lesson_page_id="b")],
    )

    assert _one(result).status == "reuse"
    assert _one(result).lesson_policy == "reuse"
    assert _one(result).lesson_page_id == "b"
    assert result.ok is True


async def test_an_override_outside_the_candidates_blocks(resolver, notion):
    """An operator may only choose among the pages review PROVED are safe. A
    free-text page id is an unreviewed destination by another name."""
    container = _container(notion)
    notion.seed(container, _TITLE, "a")
    notion.seed(container, _TITLE, "b")
    notion.seed(container, "6 Respiration", "elsewhere")
    src = source(pointer=None)

    result = await resolver.resolve(
        sources=[src],
        overrides=[dest.DestinationOverride(
            toc_entry_id=src.toc_entry_id, output_language=src.output_language,
            notion_lesson_page_id="elsewhere")],
    )

    assert _one(result).status == "blocked"
    assert "elsewhere" in (_one(result).reason or "")
    assert result.ok is False


async def test_a_blank_override_page_id_is_a_request_error(resolver, notion):
    _container(notion)
    src = source(pointer=None)

    with pytest.raises(ValueError):
        await resolver.resolve(
            sources=[src],
            overrides=[dest.DestinationOverride(
                toc_entry_id=src.toc_entry_id,
                output_language=src.output_language,
                notion_lesson_page_id="  ")],
        )


async def test_an_override_for_a_lineage_not_under_review_is_a_request_error(
    resolver, notion
):
    _container(notion)
    stray = uuid.uuid4()

    with pytest.raises(ValueError) as excinfo:
        await resolver.resolve(
            sources=[source(pointer=None)],
            overrides=[dest.DestinationOverride(
                toc_entry_id=stray, output_language="uz",
                notion_lesson_page_id="a")],
        )
    assert str(stray) in str(excinfo.value)


# ══════════════════════ the container and the mapping ════════════════════


async def test_a_missing_subject_mapping_blocks(resolver, notion, monkeypatch):
    monkeypatch.setattr(dest.settings, "notion_subject_pages", {})

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "blocked"
    assert "biology" in (_one(result).reason or "")
    assert _one(result).container_policy is None
    assert result.ok is False
    assert list(notion.calls) == [], "a mapping refusal costs no remote read"


async def test_a_missing_container_is_created_with_the_lesson(resolver, notion):
    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).container_policy == "create"
    assert _one(result).container_page_id is None
    assert _one(result).lesson_policy == "create"
    assert _one(result).lesson_page_id is None
    assert _one(result).status == "create"
    assert result.ok is True


async def test_a_published_unproven_lineage_never_creates_a_missing_container(
    resolver, notion
):
    result = await resolver.resolve(sources=[source(
        lineage_previously_published=True,
    )])

    blocked = _one(result)
    assert blocked.status == "blocked"
    assert "parallel tree" in (blocked.reason or "")
    assert blocked.container_policy is None
    assert result.ok is False


async def test_two_containers_block_the_lineage(resolver, notion):
    """Two pages named `Generated Homeworks` under one subject page. Which one
    is the archive's is a human question; guessing files a revision into a tree
    the teacher deck and V1 do not share."""
    _container(notion, page_id="container-a")
    _container(notion, page_id="container-b")

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "blocked"
    assert _CONTAINER in (_one(result).reason or "")
    assert result.ok is False


async def test_a_reuse_lesson_always_sits_under_a_reuse_container(resolver, notion):
    """`ck_regeneration_targets_notion_parent_decision` refuses
    `parent_policy='reuse'` beside `container_policy='create'`. A resolution
    that cannot be stored is not a decision."""
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    result = await resolver.resolve(
        sources=[source(pointer="lesson-1"), source(pointer=None)], overrides=())

    for resolution in result.resolutions:
        if resolution.lesson_policy == "reuse":
            assert resolution.container_policy == "reuse"
            assert resolution.container_page_id is not None
        if resolution.container_policy == "create":
            assert resolution.container_page_id is None
        if resolution.lesson_policy == "create":
            assert resolution.lesson_page_id is None


# ══════════════════ the version page: block before spend ═════════════════


async def test_an_existing_version_page_blocks_before_any_spend(resolver, notion):
    """A `Homework V3` already under the resolved Lesson Topic. Whether or not
    it carries this campaign's marker, a NEW campaign may not adopt it — so the
    refusal happens here, before a single generation is paid for, rather than at
    delivery after eleven phases have been billed."""
    container = _container(notion)
    lesson = notion.seed(container, _TITLE, "lesson-1")
    notion.seed(lesson, "Homework V3", "v3")

    result = await resolver.resolve(sources=[source(pointer="lesson-1")],
                                    overrides=())

    assert _one(result).status == "blocked"
    assert "Homework V3" in (_one(result).reason or "")
    assert result.ok is False


async def test_a_deduped_version_page_blocks_too(resolver, notion):
    """`Homework V3 (2)` is what Notion produces when the name is taken. It
    folds to the same normalized title, so it blocks on the same rule — fail
    closed, both sides of the feature normalizing identically."""
    container = _container(notion)
    lesson = notion.seed(container, _TITLE, "lesson-1")
    notion.seed(lesson, "Homework V3 (2)", "v3dup")

    result = await resolver.resolve(sources=[source(pointer="lesson-1")],
                                    overrides=())

    assert _one(result).status == "blocked"
    assert result.ok is False


async def test_another_version_under_the_lesson_does_not_block(resolver, notion):
    container = _container(notion)
    lesson = notion.seed(container, _TITLE, "lesson-1")
    notion.seed(lesson, "Homework", "v1")
    notion.seed(lesson, "Homework V2", "v2")

    result = await resolver.resolve(sources=[source(pointer="lesson-1")],
                                    overrides=())

    assert _one(result).status == "reuse"
    assert result.ok is True


async def test_a_create_policy_needs_no_version_scan(resolver, notion):
    """Nothing exists to collide with under a page that does not exist yet."""
    _container(notion)

    result = await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert _one(result).status == "create"
    assert result.ok is True


async def test_an_override_that_lands_on_a_taken_version_still_blocks(resolver,
                                                                      notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "a")
    b = notion.seed(container, _TITLE, "b")
    notion.seed(b, "Homework V3", "v3")
    src = source(pointer=None)

    result = await resolver.resolve(
        sources=[src],
        overrides=[dest.DestinationOverride(
            toc_entry_id=src.toc_entry_id, output_language=src.output_language,
            notion_lesson_page_id="b")],
    )

    assert _one(result).status == "blocked"
    assert "Homework V3" in (_one(result).reason or "")


# ══════════════════════════ the whole response ═══════════════════════════


async def test_every_target_is_reported_once(resolver, notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")
    sources = [source(pointer="lesson-1"), source(pointer=None, title="6 Respiration"),
               source(pointer=None, language="ru")]

    result = await resolver.resolve(sources=sources, overrides=())

    assert result.checked_target_count == len(sources) == 3
    assert len(result.resolutions) == 3
    assert [(r.toc_entry_id, r.output_language) for r in result.resolutions] == [
        (s.toc_entry_id, s.output_language) for s in sources]


async def test_ok_is_false_when_any_single_lineage_is_unresolved(resolver, notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "a")
    notion.seed(container, _TITLE, "b")

    result = await resolver.resolve(
        sources=[source(pointer=None, title="6 Respiration"), source(pointer=None)],
        overrides=())

    assert [r.status for r in result.resolutions] == ["create", "ambiguous"]
    assert result.ok is False


async def test_duplicate_lineages_are_a_request_error(resolver, notion):
    _container(notion)
    shared = uuid.uuid4()

    with pytest.raises(ValueError) as excinfo:
        await resolver.resolve(
            sources=[source(pointer=None, toc_entry_id=shared),
                     source(pointer=None, toc_entry_id=shared)],
            overrides=())
    assert str(shared) in str(excinfo.value)


async def test_the_same_lesson_in_two_languages_is_two_lineages(resolver, notion):
    shared = uuid.uuid4()
    uz = _container(notion)
    notion.seed(uz, _TITLE, "uz-lesson")
    ru = _container(notion, parent=_SUBJECT_RU, page_id="container-ru")
    notion.seed(ru, _TITLE, "ru-lesson")

    result = await resolver.resolve(
        sources=[source(pointer=None, toc_entry_id=shared),
                 source(pointer=None, toc_entry_id=shared, language="ru")],
        overrides=())

    assert [r.lesson_page_id for r in result.resolutions] == [
        "uz-lesson", "ru-lesson"]
    assert result.ok is True


# ═══════════════════════════════ the bound ═══════════════════════════════


async def test_more_targets_than_the_bound_are_refused_not_truncated(resolver,
                                                                     notion):
    """Scanning a prefix would return a result that looks complete and silently
    approves nothing for the rest."""
    _container(notion)
    sources = [source(pointer=None) for _ in range(6)]

    with pytest.raises(ValueError) as excinfo:
        await resolver.resolve(sources=sources, overrides=(), maximum_targets=5)
    assert "6" in str(excinfo.value) and "5" in str(excinfo.value)
    assert list(notion.calls) == [], "a refused request scans nothing"


async def test_the_default_bound_is_five_hundred(resolver):
    import inspect

    signature = inspect.signature(dest.resolve_destinations)
    assert signature.parameters["maximum_targets"].default == 500


async def test_exactly_the_bound_is_allowed(resolver, notion):
    _container(notion)
    sources = [source(pointer=None) for _ in range(5)]

    result = await resolver.resolve(sources=sources, overrides=(),
                                    maximum_targets=5)
    assert result.checked_target_count == 5


# ════════════════════ readiness, transport, and raising ══════════════════


@pytest.mark.parametrize(
    "enabled, key",
    [(False, _USABLE_KEY), (True, ""), (True, "   "), (True, "not-a-notion-key")],
    ids=["disabled", "no-key", "blank-key", "wrong-shape"],
)
async def test_an_unusable_head_raises_non_retryably_and_builds_no_client(
    resolver, notion, monkeypatch, enabled, key
):
    monkeypatch.setattr(dest.settings, "notion_enabled", enabled)
    monkeypatch.setattr(dest.settings, "notion_api_key", key)
    _container(notion)

    with pytest.raises(dest.DestinationServiceUnavailable) as excinfo:
        await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert excinfo.value.retryable is False
    assert resolver.factory_calls == 0, "no client is built without a destination"
    assert list(notion.calls) == []


async def test_a_rate_limited_scan_raises_retryably_rather_than_returning(
    resolver, notion
):
    """A partial scan has UNKNOWN resolutions. Returning `ok=False` would make
    it indistinguishable from a reviewed refusal an operator could act on."""
    boom = RuntimeError("notion 429: rate_limited")

    def _throttled(_parent_id):
        raise boom

    notion.get_child_pages = _throttled
    _container(notion)

    with pytest.raises(dest.DestinationServiceUnavailable) as excinfo:
        await resolver.resolve(sources=[source(pointer=None)], overrides=())

    assert excinfo.value.retryable is True
    assert excinfo.value.__cause__ is boom


async def test_the_readiness_predicate_is_the_publishers_own(resolver):
    """One predicate, three callers. A second copy is how a head gates the loop
    on one answer while review promises another."""
    import app.services.regeneration_notion_readiness as readiness
    import app.services.regeneration_publisher as publisher

    assert dest.publication_unavailable_reason is (
        readiness.publication_unavailable_reason)
    assert publisher.publication_unavailable_reason is (
        readiness.publication_unavailable_reason)


# ═══════════════════ one client, one hop, cached reads ═══════════════════


async def test_the_whole_scan_runs_off_the_event_loop_on_one_client(resolver,
                                                                    notion):
    """`NotionClientWrapper.__init__` opens an HTTP client, so even building it
    is worker-thread work — and a fresh client per lesson would re-pay the
    handshake and reset the rate limiter that keeps the scan under Notion's
    3 req/s."""
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    result = await resolver.resolve(
        sources=[source(pointer="lesson-1") for _ in range(3)], overrides=())

    assert result.checked_target_count == 3
    assert resolver.factory_calls == 1
    assert resolver.off_loop(), "no Notion call may run on the event loop"


async def test_the_scan_makes_exactly_one_thread_hop(resolver, notion,
                                                     monkeypatch):
    hops: list = []
    real_to_thread = asyncio.to_thread

    async def _counting(func, /, *args, **kwargs):
        hops.append(getattr(func, "__name__", repr(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(dest.asyncio, "to_thread", _counting)
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    await resolver.resolve(
        sources=[source(pointer="lesson-1"), source(pointer=None,
                                                    title="6 Respiration")],
        overrides=())

    assert len(hops) == 1, f"one bounded scan, one hop; got {hops}"


async def test_every_page_is_read_once_per_call(resolver, notion):
    """Fifty lessons in one subject must not re-read the subject page and the
    container fifty times — that is 100 avoidable requests at 0.35s apiece."""
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    await resolver.resolve(
        sources=[source(pointer="lesson-1") for _ in range(10)], overrides=())

    reads = [call[1] for call in notion.calls if call[0] == "get_child_pages"]
    assert reads.count(_SUBJECT_UZ) == 1
    assert reads.count(container) == 1
    assert reads.count("lesson-1") == 1


async def test_the_preflight_never_writes(resolver, notion):
    """The whole write surface at once. A preflight that creates the container
    it was asked about has changed the answer it is reporting."""
    container = _container(notion, page_id="container-a")
    notion.seed(container, _TITLE, "lesson-1")
    _container(notion, parent=_SUBJECT_RU, page_id="container-ru")

    await resolver.resolve(
        sources=[source(pointer="lesson-1"),
                 source(pointer=None, title="6 Respiration"),
                 source(pointer=None, language="ru"),
                 source(pointer=None, subject="unmapped")],
        overrides=())

    assert [c[0] for c in notion.calls if c[0] in _WRITES] == []
    assert set(c[0] for c in notion.calls) <= {
        "get_child_pages", "get_block_children", "get_page_title",
        "get_page_parent"}


# ═════════════════════════════ the digest ════════════════════════════════


def _resolution(**overrides) -> "dest.DestinationResolution":
    base = dict(
        toc_entry_id=uuid.UUID(int=1), output_language="uz", lesson_title=_TITLE,
        status="reuse", container_policy="reuse", container_page_id="container",
        lesson_policy="reuse", lesson_page_id="lesson-1", candidates=(),
        reason=None,
    )
    base.update(overrides)
    return dest.DestinationResolution(**base)


def test_the_digest_ignores_candidate_order_and_reason():
    """The digest is what the operator's approval is bound to. If it moved with
    the order Notion happened to return two pages in, a re-read would revoke an
    approval nothing about the decision had changed."""
    a = dest.DestinationCandidate(page_id="a", title=_TITLE)
    b = dest.DestinationCandidate(page_id="b", title=_TITLE)
    one = _resolution(candidates=(a, b), reason="two matches")
    two = _resolution(candidates=(b, a), reason=None)

    assert dest.destination_digest([one], requested_version=3) == (
        dest.destination_digest([two], requested_version=3))


def test_the_digest_is_stable_across_input_order():
    first = _resolution(toc_entry_id=uuid.UUID(int=1))
    second = _resolution(toc_entry_id=uuid.UUID(int=2), lesson_page_id="lesson-2")

    assert dest.destination_digest([first, second], requested_version=3) == (
        dest.destination_digest([second, first], requested_version=3))


@pytest.mark.parametrize(
    "change",
    [
        {"lesson_page_id": "somewhere-else"},
        {"lesson_policy": "create", "lesson_page_id": None, "status": "create"},
        {"container_page_id": "another-container"},
        {"container_policy": "create", "container_page_id": None},
        {"lesson_title": "7 Photosynthesis · p.41"},
        {"output_language": "ru"},
        {"toc_entry_id": uuid.UUID(int=9)},
        {"status": "blocked"},
    ],
)
def test_any_change_to_the_decision_moves_the_digest(change):
    baseline = dest.destination_digest([_resolution()], requested_version=3)
    assert dest.destination_digest(
        [_resolution(**change)], requested_version=3) != baseline


def test_the_requested_version_is_part_of_the_digest():
    """An approval of `Homework V3` is not an approval of `Homework V4`."""
    assert dest.destination_digest([_resolution()], requested_version=3) != (
        dest.destination_digest([_resolution()], requested_version=4))


def test_the_digest_is_a_sha256_hex_string():
    digest = dest.destination_digest([_resolution()], requested_version=3)
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


async def test_the_preflight_carries_the_digest_of_its_own_resolutions(resolver,
                                                                       notion):
    container = _container(notion)
    notion.seed(container, _TITLE, "lesson-1")

    result = await resolver.resolve(sources=[source(pointer="lesson-1")],
                                    overrides=())

    assert result.digest == dest.destination_digest(
        result.resolutions, requested_version=3)


# ═══════════════════════ scalars only, by contract ═══════════════════════


def test_every_result_type_is_a_frozen_dataclass():
    """The module accepts and returns scalars so a caller can close its DB
    transaction before the remote scan — an ORM row carried in here would turn
    a worker-thread read into surprise database I/O on a closed session."""
    import dataclasses

    for kind in (dest.DestinationSource, dest.DestinationOverride,
                 dest.DestinationCandidate, dest.DestinationResolution,
                 dest.DestinationPreflight):
        assert dataclasses.is_dataclass(kind)
        assert kind.__dataclass_params__.frozen is True
