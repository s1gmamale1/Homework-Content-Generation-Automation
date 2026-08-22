"""Marker-backed versioned Notion writer (`Homework V{n}`).

Every test drives the real writer against an in-memory fake that models the
subset of `NotionClientWrapper` the writer touches, including Notion's own
"a page keeps its content" and "child_page blocks survive a clear" behaviours.
No network, no DB, no credentials.

The load-bearing properties under test are the ones that protect already
published content: a same-title page whose marker does not match is a *visible*
failure, never an overwrite; a completed publication re-run is a no-op; and V1's
`Homework` page is never read for adoption, cleared or written.
"""

from __future__ import annotations

import copy
import json
from uuid import UUID

import pytest

import app.services.notion_versioned_homework as nvh
from app.services.notion import blocks as nblocks
from app.services.notion_versioned_homework import (
    HomeworkRevisionMarker,
    VersionPageCollision,
    decode_revision_marker,
    encode_revision_marker,
    write_or_adopt_versioned_homework,
)

TOC = UUID("11111111-1111-1111-1111-111111111111")
JOB = UUID("22222222-2222-2222-2222-222222222222")
CAMPAIGN = UUID("33333333-3333-3333-3333-333333333333")
OTHER_JOB = UUID("44444444-4444-4444-4444-444444444444")


def marker(version: int = 2, language: str = "uz", job: UUID = JOB) -> HomeworkRevisionMarker:
    return HomeworkRevisionMarker(
        toc_entry_id=TOC,
        output_language=language,
        revision_job_id=job,
        campaign_id=CAMPAIGN,
        publication_version=version,
    )


PHASES = {
    "case-based-preview": "# Preview\n\nBody.",
    "flashcards": "# Cards\n\n- a / b",
    "memory-check": "# Check\n\nq?",
    "practice-rlc": "# RLC\n\ndo it",
    "practice-tictactoe": "# TTT\n\ngrid",
    "boss-arena": "# Boss\n\nQ1",
    "reflection": "# Reflection\n\nwhat stuck?",
}


class FakeNotion:
    """In-memory Notion. Pages are block trees; a sub-page shows up in its
    parent's block list as a `child_page` block, exactly as the real API
    returns it — which is what makes `page_has_content` and
    `clear_content_blocks` behave like the real ones."""

    def __init__(self) -> None:
        self.blocks: dict[str, list[dict]] = {}   # page_id -> its blocks
        self.titles: dict[str, str] = {}          # page_id -> title
        self.parents: dict[str, str] = {}         # page_id -> parent page_id
        self.calls: list[tuple] = []
        self._n = 0

    # -- test setup helpers -------------------------------------------------
    def add_page(self, parent_id: str, title: str, body: list[dict] | None = None) -> str:
        self._n += 1
        pid = f"pg{self._n}"
        self.titles[pid] = title
        self.parents[pid] = parent_id
        self.blocks[pid] = list(body or [])
        for blk in self.blocks[pid]:
            blk.setdefault("id", self._new_block_id())
        self.blocks.setdefault(parent_id, []).append(
            {"id": f"cp::{pid}", "type": "child_page", "child_page": {"title": title}}
        )
        # the child_page block id IS the page id in Notion; mirror that
        self.blocks[parent_id][-1]["id"] = pid
        return pid

    def _new_block_id(self) -> str:
        self._n += 1
        return f"blk{self._n}"

    def count(self, method: str) -> int:
        return sum(1 for c in self.calls if c[0] == method)

    def body_of(self, page_id: str) -> list[dict]:
        return [b for b in self.blocks.get(page_id, []) if b.get("type") != "child_page"]

    def child_titles(self, page_id: str) -> list[str]:
        return [b["child_page"]["title"] for b in self.blocks.get(page_id, [])
                if b.get("type") == "child_page"]

    # -- NotionClientWrapper subset ----------------------------------------
    def get_block_children(self, block_id: str) -> list[dict]:
        self.calls.append(("get_block_children", block_id))
        return [dict(b) for b in self.blocks.get(block_id, [])]

    def get_child_pages(self, parent_id: str) -> list[dict]:
        self.calls.append(("get_child_pages", parent_id))
        return [{"id": b["id"], "title": b["child_page"]["title"], "type": "child_page"}
                for b in self.blocks.get(parent_id, []) if b.get("type") == "child_page"]

    def create_page(self, parent_id: str, title: str, children: list[dict] | None = None) -> dict:
        # The real client POSTs JSON: it neither mutates the caller's block
        # dicts nor stores the caller's objects. Deep-copying on both sides
        # keeps that true here — otherwise the recorded call args and the
        # page's stored blocks would be the SAME objects and any assertion
        # comparing them would be vacuously true.
        kids = list(children or [])
        self.calls.append(("create_page", parent_id, title, copy.deepcopy(kids)))
        pid = self.add_page(parent_id, title, copy.deepcopy(kids))
        return {"id": pid}

    def page_has_content(self, page_id: str) -> bool:
        self.calls.append(("page_has_content", page_id))
        return bool(self.body_of(page_id))

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        self.calls.append(("append_block_children", block_id, children))
        for blk in children:
            new = dict(blk)
            new["id"] = self._new_block_id()
            self.blocks.setdefault(block_id, []).append(new)
        return {"results": []}

    def delete_block(self, block_id: str) -> None:
        self.calls.append(("delete_block", block_id))
        for page_id, body in self.blocks.items():
            self.blocks[page_id] = [b for b in body if b.get("id") != block_id]

    def clear_content_blocks(self, page_id: str) -> int:
        self.calls.append(("clear_content_blocks", page_id))
        keep = [b for b in self.blocks.get(page_id, []) if b.get("type") == "child_page"]
        removed = len(self.blocks.get(page_id, [])) - len(keep)
        self.blocks[page_id] = keep
        return removed

    def upload_bytes(self, data: bytes, file_name: str, content_type: str) -> str:
        self.calls.append(("upload_bytes", file_name, content_type))
        return f"upl::{file_name}"


def _lesson(fake: FakeNotion) -> str:
    """A Lesson Topic page holding V1's `Homework` sub-page with real content."""
    lesson_id = fake.add_page("subject", "1-§ Sonli ifodalar")
    hw = fake.add_page(lesson_id, "Homework")
    fake.blocks[hw].append({"id": "v1body", "type": "paragraph",
                            "paragraph": {"rich_text": [{"type": "text",
                                                         "text": {"content": "V1 content"}}]}})
    return lesson_id


def _marker_page(fake: FakeNotion, lesson_id: str, mk: HomeworkRevisionMarker,
                 title: str | None = None) -> str:
    """An already-created version page carrying `mk` as its first block."""
    blk = nblocks.make_paragraph(encode_revision_marker(mk))
    return fake.add_page(lesson_id, title or f"Homework V{mk.publication_version}", [blk])


def _page_tree(fake: FakeNotion, root_id: str) -> set[str]:
    """`root_id` plus every page created beneath it, transitively."""
    tree = {root_id}
    changed = True
    while changed:
        changed = False
        for page_id, parent in fake.parents.items():
            if parent in tree and page_id not in tree:
                tree.add(page_id)
                changed = True
    return tree


def _calls_touching(calls: list[tuple], pages: set[str]) -> list[tuple]:
    """Every recorded call whose target page/block id is inside `pages`."""
    return [c for c in calls if len(c) > 1 and c[1] in pages]


def _write(fake: FakeNotion, lesson_id: str, *, mk: HomeworkRevisionMarker | None = None,
           stored: str | None = None, phase_md: dict[str, str] | None = None) -> str:
    return write_or_adopt_versioned_homework(
        client=fake,
        lesson_page_id=lesson_id,
        phase_md=PHASES if phase_md is None else phase_md,
        marker=mk or marker(),
        stored_page_id=stored,
    )


# --- marker encoding / decoding --------------------------------------------


def test_encode_revision_marker_is_deterministic_and_field_sensitive():
    assert encode_revision_marker(marker()) == encode_revision_marker(marker())
    assert encode_revision_marker(marker()) != encode_revision_marker(marker(version=3))
    assert encode_revision_marker(marker()) != encode_revision_marker(marker(language="ru"))
    assert encode_revision_marker(marker()) != encode_revision_marker(marker(job=OTHER_JOB))
    # every field is actually carried, not just hashed away
    text = encode_revision_marker(marker())
    assert str(TOC) in text and str(JOB) in text and str(CAMPAIGN) in text
    assert '"output_language":"uz"' in text and '"publication_version":2' in text


def test_marker_round_trips_through_a_paragraph_block():
    mk = marker()
    blk = nblocks.make_paragraph(encode_revision_marker(mk))
    assert decode_revision_marker([blk]) == mk


def test_decode_tolerates_plain_text_readback_and_multiple_segments():
    """Notion read-back carries `plain_text`; long markers arrive chunked."""
    text = encode_revision_marker(marker())
    half = len(text) // 2
    blk = {"type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "plain_text": text[:half]},
        {"type": "text", "plain_text": text[half:]},
    ]}}
    assert decode_revision_marker([blk]) == marker()


def test_decode_finds_the_marker_among_other_blocks():
    body = [nblocks.make_divider(),
            nblocks.make_paragraph("some homework text"),
            nblocks.make_paragraph(encode_revision_marker(marker()))]
    assert decode_revision_marker(body) == marker()


@pytest.mark.parametrize("body", [
    [],
    [nblocks.make_paragraph("just prose")],
    [nblocks.make_divider()],
    [{"type": "paragraph", "paragraph": {}}],
])
def test_decode_returns_none_when_no_marker_present(body):
    assert decode_revision_marker(body) is None


@pytest.mark.parametrize("payload", [
    "{not json",
    json.dumps({"toc_entry_id": str(TOC)}),                       # missing fields
    json.dumps({"toc_entry_id": "not-a-uuid", "output_language": "uz",
                "revision_job_id": str(JOB), "campaign_id": str(CAMPAIGN),
                "publication_version": 2}),
    json.dumps({"toc_entry_id": str(TOC), "output_language": "uz",
                "revision_job_id": str(JOB), "campaign_id": str(CAMPAIGN),
                "publication_version": "2"}),                     # wrong type
    json.dumps([str(TOC), "uz"]),                                 # not an object
])
def test_decode_never_raises_on_malformed_marker(payload):
    blk = nblocks.make_paragraph(f"{nvh.MARKER_SENTINEL} {payload}")
    assert decode_revision_marker([blk]) is None


def test_decode_rejects_a_bool_publication_version():
    """A JSON `true` must not decode as version 1.

    `isinstance(True, int)` is True in Python and `True == 1`, so a dataclass
    carrying `publication_version=True` compares EQUAL to one carrying `1`.
    Without an explicit bool guard, a corrupt/hand-edited marker would decode
    successfully and impersonate version 1 for adoption and digest purposes."""
    assert marker(version=True) == marker(version=1)   # why the guard is needed
    payload = json.dumps({
        "toc_entry_id": str(TOC), "output_language": "uz",
        "revision_job_id": str(JOB), "campaign_id": str(CAMPAIGN),
        "publication_version": True,
    })
    blk = nblocks.make_paragraph(f"{nvh.MARKER_SENTINEL} {payload}")
    assert decode_revision_marker([blk]) is None


# --- creation ---------------------------------------------------------------


def test_creates_sibling_version_page_with_marker_as_first_block():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    page_id = _write(fake, lesson_id)

    create = next(c for c in fake.calls if c[0] == "create_page" and c[2] == "Homework V2")
    assert create[1] == lesson_id                      # sibling of V1, under the lesson
    # The marker must be the FIRST block, not merely present: adoption reads the
    # head of the page, and a page whose first block is anything else is not
    # provably ours. Compare by TEXT — the fake, like Notion, hands back its own
    # objects, so an identity comparison here would prove nothing.
    assert create[3], "the version page must be created WITH its marker, not stamped after"
    assert nvh._block_text(create[3][0]) == encode_revision_marker(marker())
    assert nvh._block_text(fake.body_of(page_id)[0]) == encode_revision_marker(marker())
    assert decode_revision_marker(create[3]) == marker()
    assert "Homework V2" in fake.child_titles(lesson_id)
    assert "Homework" in fake.child_titles(lesson_id)  # V1 still there


def test_created_page_uses_the_same_grouped_layout_as_v1():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    page_id = _write(fake, lesson_id)
    assert fake.child_titles(page_id) == [
        "Case-Based Preview", "Flashcards", "Gamified Practices", "Boss Arena", "Reflection",
    ]
    container = next(b["id"] for b in fake.blocks[page_id]
                     if b.get("type") == "child_page"
                     and b["child_page"]["title"] == "Gamified Practices")
    assert fake.child_titles(container) == ["Real-Life Challenge", "TicTacToe"]


def test_v2_and_v3_are_distinct_siblings_and_v1_is_never_touched():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    v1_id = next(b["id"] for b in fake.blocks[lesson_id]
                 if b.get("type") == "child_page" and b["child_page"]["title"] == "Homework")
    v1_before = list(fake.blocks[v1_id])

    v2 = _write(fake, lesson_id, mk=marker(version=2))
    v3 = _write(fake, lesson_id, mk=marker(version=3, job=OTHER_JOB))

    assert v2 != v3
    assert fake.child_titles(lesson_id) == ["Homework", "Homework V2", "Homework V3"]
    assert fake.blocks[v1_id] == v1_before
    assert not any(c[1] == v1_id for c in fake.calls
                   if c[0] in ("append_block_children", "clear_content_blocks",
                               "page_has_content", "get_block_children"))


def test_uz_and_ru_v2_markers_are_independent():
    """Same lesson + same version, different output language: the RU publisher
    must NOT adopt the UZ page — the marker differs, so it is a collision."""
    uz, ru = marker(language="uz"), marker(language="ru")
    assert encode_revision_marker(uz) != encode_revision_marker(ru)
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    _write(fake, lesson_id, mk=uz)
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id, mk=ru)


def test_uz_and_ru_v2_publish_independently_under_their_own_lesson_pages():
    """Spec section 9: `UZ V2` and `RU V2` are valid independent publications.

    In production the two languages resolve to different Notion subject trees
    (`notion_archive._resolve_subject_page_id(..., language=...)`), so each
    gets its own Lesson Topic page. Both must publish a full `Homework V2`,
    and neither may read, adopt or write the other's page."""
    uz_mk, ru_mk = marker(language="uz"), marker(language="ru", job=OTHER_JOB)
    fake = FakeNotion()
    uz_lesson, ru_lesson = _lesson(fake), _lesson(fake)

    uz_page = _write(fake, uz_lesson, mk=uz_mk)
    uz_blocks_before_ru = copy.deepcopy(fake.blocks)
    boundary = len(fake.calls)          # every later call belongs to the RU publish
    ru_page = _write(fake, ru_lesson, mk=ru_mk)

    assert uz_page != ru_page
    assert fake.child_titles(uz_lesson) == ["Homework", "Homework V2"]
    assert fake.child_titles(ru_lesson) == ["Homework", "Homework V2"]
    assert nvh.decode_revision_marker(fake.blocks[uz_page]).output_language == "uz"
    assert nvh.decode_revision_marker(fake.blocks[ru_page]).output_language == "ru"
    # both trees fully rendered, independently
    for page in (uz_page, ru_page):
        assert fake.child_titles(page) == [
            "Case-Based Preview", "Flashcards", "Gamified Practices",
            "Boss Arena", "Reflection",
        ]
    # Each publication is identified independently: its own marker, and its own
    # completion stamp. Two languages must never share a digest, or one
    # language's completed publish would read as "already complete" for the
    # other and silently skip rendering it.
    uz_digest = nvh.decode_completion_digest(fake.blocks[uz_page])
    ru_digest = nvh.decode_completion_digest(fake.blocks[ru_page])
    assert uz_digest == nvh.completion_digest(uz_mk, PHASES)
    assert ru_digest == nvh.completion_digest(ru_mk, PHASES)
    assert uz_digest != ru_digest

    # Isolation: not one call in the RU segment addressed any page in the UZ
    # tree, for ANY method, and the UZ tree is byte-identical afterwards.
    uz_tree = _page_tree(fake, uz_lesson)
    ru_calls = fake.calls[boundary:]
    assert ru_calls, "the RU publish must actually have made calls"
    assert _calls_touching(ru_calls, uz_tree) == []
    for page_id in uz_tree:
        assert fake.blocks[page_id] == uz_blocks_before_ru[page_id]
    # ...and that predicate is not vacuous: run it over the UZ segment, where
    # the UZ tree IS the target, and it finds plenty.
    assert _calls_touching(fake.calls[:boundary], uz_tree)


# --- stored page id (R4) -----------------------------------------------------


def test_stored_page_id_is_revalidated_then_reused():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())
    assert _write(fake, lesson_id, stored=root) == root
    assert fake.count("create_page") >= 1              # leaves were created
    assert not any(c[0] == "create_page" and c[2] == "Homework V2" for c in fake.calls)
    assert not any(c[0] == "get_child_pages" and c[1] == lesson_id for c in fake.calls)


def test_stored_page_id_with_wrong_marker_is_a_collision_and_writes_nothing():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker(job=OTHER_JOB))
    before = list(fake.blocks[root])
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id, stored=root)
    assert fake.blocks[root] == before
    assert fake.count("create_page") == 0
    assert fake.count("append_block_children") == 0
    assert fake.count("clear_content_blocks") == 0
    assert fake.count("delete_block") == 0
    # never falls through to enumeration or creation
    assert not any(c[0] == "get_child_pages" and c[1] == lesson_id for c in fake.calls)


def test_stored_page_id_without_any_marker_is_a_collision():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = fake.add_page(lesson_id, "Homework V2", [nblocks.make_paragraph("hand-written")])
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id, stored=root)
    assert fake.count("create_page") == 0


# --- title enumeration + adoption (R5) --------------------------------------


def test_same_title_page_with_different_marker_is_a_collision_never_overwritten():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    foreign = _marker_page(fake, lesson_id, marker(version=2, job=OTHER_JOB))
    before = list(fake.blocks[foreign])
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id)
    assert fake.blocks[foreign] == before
    assert fake.count("clear_content_blocks") == 0
    assert fake.count("append_block_children") == 0


def test_same_title_page_with_no_marker_is_a_collision():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    squatter = fake.add_page(lesson_id, "Homework V2",
                             [nblocks.make_paragraph("a human wrote this")])
    before = list(fake.blocks[squatter])
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id)
    assert fake.blocks[squatter] == before


def test_two_same_title_candidates_are_a_collision_never_guessed():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    _marker_page(fake, lesson_id, marker())
    # Notion's own dedup suffix must not smuggle a second page in as a non-match
    _marker_page(fake, lesson_id, marker(), title="Homework V2 (2)")
    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id)
    assert fake.count("append_block_children") == 0


def test_adopts_marker_matching_page_and_repairs_a_root_only_crash():
    """Crash after `create_page`, before any leaf was written."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())
    assert _write(fake, lesson_id) == root
    assert fake.child_titles(root) == [
        "Case-Based Preview", "Flashcards", "Gamified Practices", "Boss Arena", "Reflection",
    ]
    assert nvh.decode_revision_marker(fake.blocks[root]) == marker()   # marker survived


# --- repair + completion digest (R3/R6/R7) ----------------------------------


def test_partially_populated_leaf_is_cleared_and_rewritten():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())
    stale_leaf = fake.add_page(root, "Boss Arena",
                               [nblocks.make_paragraph("half-written boss content")])
    _write(fake, lesson_id, stored=root)
    assert ("clear_content_blocks", stale_leaf) in fake.calls
    body_text = json.dumps(fake.body_of(stale_leaf))
    assert "half-written boss content" not in body_text
    assert "Boss" in body_text                    # fresh content landed


def test_completion_digest_makes_a_retry_a_total_no_op():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)
    fake.calls.clear()
    assert _write(fake, lesson_id, stored=root) == root
    for method in ("upload_bytes", "create_page", "append_block_children",
                   "clear_content_blocks", "delete_block"):
        assert fake.count(method) == 0, f"{method} was called on a completed retry"


def test_completion_is_stamped_only_after_every_leaf_is_populated():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)
    # the completion block is the LAST write, and it lands on the root
    writes = [c for c in fake.calls if c[0] == "append_block_children"]
    assert writes[-1][1] == root
    assert nvh.decode_completion_digest(writes[-1][2]) is not None
    assert not any(nvh.decode_completion_digest(c[2]) for c in writes[:-1])


def test_changed_payload_invalidates_completion_and_rewrites():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)
    fake.calls.clear()
    changed = dict(PHASES, reflection="# Reflection\n\nsomething else entirely")
    assert _write(fake, lesson_id, stored=root, phase_md=changed) == root
    assert fake.count("append_block_children") > 0
    assert json.dumps(fake.blocks[root]).count(nvh.COMPLETION_SENTINEL) == 1


def test_conflicting_completion_block_is_deleted_before_anything_is_rendered():
    """Ordering, not just eventual removal: the delete must precede the FIRST
    render call. Deleting afterwards would leave a window in which a crash
    mid-render left the page still claiming the old completeness over
    half-written content — exactly what this ordering exists to prevent."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())
    stale = nblocks.make_paragraph(f"{nvh.COMPLETION_SENTINEL} deadbeef")
    fake.append_block_children(root, [stale])
    stale_id = fake.blocks[root][-1]["id"]
    fake.calls.clear()

    _write(fake, lesson_id, stored=root)

    methods = [c[0] for c in fake.calls]
    assert ("delete_block", stale_id) in fake.calls
    renders = [i for i, m in enumerate(methods)
               if m in ("create_page", "append_block_children",
                        "clear_content_blocks", "upload_bytes")]
    assert renders, "the repair must actually have rendered something"
    assert methods.index("delete_block") < renders[0]
    assert json.dumps(fake.blocks[root]).count(nvh.COMPLETION_SENTINEL) == 1
    assert "deadbeef" not in json.dumps(fake.blocks[root])


def test_digest_ignores_phase_dict_insertion_order():
    """R3: the digest binds the order the writer RENDERS in (the layout), so a
    differently-ordered dict of identical content must not force a rewrite."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)
    fake.calls.clear()
    reordered = {k: PHASES[k] for k in reversed(list(PHASES))}
    _write(fake, lesson_id, stored=root, phase_md=reordered)
    assert fake.count("append_block_children") == 0


def test_digest_binds_the_marker_not_only_the_payload():
    d1 = nvh.completion_digest(marker(), PHASES)
    assert d1 == nvh.completion_digest(marker(), PHASES)
    assert d1 != nvh.completion_digest(marker(version=3), PHASES)
    assert d1 != nvh.completion_digest(marker(language="ru"), PHASES)
    assert d1 != nvh.completion_digest(marker(), dict(PHASES, reflection="# R\n\nother"))


# --- invariants (R6/R8/R9) ---------------------------------------------------


def test_root_version_page_is_never_cleared():
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())
    fake.add_page(root, "Reflection", [nblocks.make_paragraph("stale")])
    _write(fake, lesson_id, stored=root)
    assert ("clear_content_blocks", root) not in fake.calls
    assert nvh.decode_revision_marker(fake.blocks[root]) == marker()


def test_client_errors_propagate_unchanged():
    class Boom(FakeNotion):
        def upload_bytes(self, data, file_name, content_type):
            raise RuntimeError("notion 429")

    fake = Boom()
    lesson_id = _lesson(fake)
    with pytest.raises(RuntimeError, match="notion 429"):
        _write(fake, lesson_id)


# --- boundary guards (programming errors, not collisions) --------------------


@pytest.mark.parametrize("empty", [
    {},                                             # nothing at all
    {"not-a-phase": "x"},                           # nothing the layout renders
    {"unknown": "", "practice-nope": "y"},
])
def test_payload_that_renders_nothing_is_refused_before_any_remote_call(empty):
    """A publication that would render zero leaves must never create a page.

    Spec section 9: a reserved version "is never reused". A page carrying only
    the two machine markers would be reported published, and every retry would
    be a permanent no-op (the digest matches), so the version is irreversibly
    burnt on an empty page. This is a caller bug, hence `ValueError` — NOT a
    `VersionPageCollision`, which means "a page I must not touch"."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    before = copy.deepcopy(fake.blocks)
    fake.calls.clear()

    with pytest.raises(ValueError) as exc:
        _write(fake, lesson_id, phase_md=empty)

    assert not isinstance(exc.value, VersionPageCollision)
    text = str(exc.value)
    assert str(TOC) in text and "uz" in text and "2" in text
    # fired before `_resolve_root`: not one remote call, not one page
    assert fake.calls == []
    assert fake.blocks == before
    assert fake.child_titles(lesson_id) == ["Homework"]


@pytest.mark.parametrize("bad_version", [1, 0, -3])
def test_publication_version_below_two_is_refused_before_any_remote_call(bad_version):
    """Spec section 9: the first allocated database version is 2, and V1's page
    is not renamed. `version_page_title(1)` is `Homework V1`, which normalizes
    to neither `homework` nor any page we own — so a caller passing 1/0/-3 would
    silently mint a stray sibling and burn a version on it."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    before = copy.deepcopy(fake.blocks)
    fake.calls.clear()

    with pytest.raises(ValueError) as exc:
        _write(fake, lesson_id, mk=marker(version=bad_version))

    assert not isinstance(exc.value, VersionPageCollision)
    assert str(bad_version) in str(exc.value)
    assert fake.calls == []
    assert fake.blocks == before
    assert fake.child_titles(lesson_id) == ["Homework"]   # no stray sibling


def test_version_one_is_still_decodable_and_titleable():
    """The v>=2 guard lives at the WRITER boundary only.

    `version_page_title` stays a pure formatter, and `decode_revision_marker`
    stays total: a hostile or hand-edited marker claiming version 1 must decode
    to "not our marker's value" (a collision upstream), never raise."""
    assert nvh.version_page_title(1) == "Homework V1"
    blk = nblocks.make_paragraph(encode_revision_marker(marker(version=1)))
    assert decode_revision_marker([blk]) == marker(version=1)


def test_conflicting_stamp_on_the_matching_digest_path_is_a_collision():
    """Two coexisting stamps mean our digest no longer proves the CONTENT is ours.

    A publisher stamps only after it has rendered, and it deletes a conflicting
    stamp *before* it renders. So the only way both stamps coexist is that the
    other publisher read this page before our stamp landed and then rendered its
    own payload over ours: our stamp is evidence of an older render, not of the
    bytes now on the page.

    Deleting the other stamp and returning would (a) write on the path the spec
    says performs no writes, and (b) report "V2 published with our payload" over
    content that is somebody else's — and which of the two publishers gets
    blessed would depend only on which one retried first. Refuse, touch nothing,
    let an operator look."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)                       # fully published, our digest
    foreign = nblocks.make_paragraph(f"{nvh.COMPLETION_SENTINEL} deadbeef")
    fake.append_block_children(root, [foreign])
    before = copy.deepcopy(fake.blocks)
    leaf_titles_before = fake.child_titles(root)
    fake.calls.clear()

    with pytest.raises(VersionPageCollision) as exc:
        _write(fake, lesson_id, stored=root)

    assert "deadbeef" in str(exc.value)                  # the operator is told which
    for method in ("upload_bytes", "create_page", "append_block_children",
                   "clear_content_blocks", "delete_block"):
        assert fake.count(method) == 0, f"{method} was called on a conflicted page"
    assert fake.blocks == before                         # BOTH stamps left intact
    assert fake.child_titles(root) == leaf_titles_before


def test_conflicted_page_is_refused_symmetrically_for_both_publishers():
    """The refusal must not depend on which digest-bearing retry runs first.

    Same page, same two stamps; the publisher holding the *other* payload gets
    the same visible collision, not a silent adoption of its own stamp."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _write(fake, lesson_id)                       # our payload rendered + stamped
    other_md = dict(PHASES, reflection="# Reflection\n\nsomeone else's payload")
    other_digest = nvh.completion_digest(marker(), other_md)
    fake.append_block_children(
        root, [nblocks.make_paragraph(nvh.encode_completion_marker(other_digest))])
    before = copy.deepcopy(fake.blocks)
    fake.calls.clear()

    for payload in (PHASES, other_md):
        with pytest.raises(VersionPageCollision):
            _write(fake, lesson_id, stored=root, phase_md=payload)
    assert fake.blocks == before
    assert [c[0] for c in fake.calls] == ["get_block_children", "get_block_children"]


def test_conflicting_stamp_without_a_block_id_is_a_collision_on_the_rebuild_path():
    """The rebuild path may delete a conflicting stamp only because it replaces
    it with content it renders itself. A stamp with no addressable block id
    cannot be deleted, so rendering on would leave the page carrying somebody
    else's `complete` claim over our content — the exact state the delete-first
    ordering exists to prevent. Refuse instead of half-cleaning."""
    fake = FakeNotion()
    lesson_id = _lesson(fake)
    root = _marker_page(fake, lesson_id, marker())       # our marker, nothing rendered yet
    fake.blocks[root].append(
        nblocks.make_paragraph(f"{nvh.COMPLETION_SENTINEL} deadbeef"))   # no "id" key
    before = copy.deepcopy(fake.blocks)
    fake.calls.clear()

    with pytest.raises(VersionPageCollision):
        _write(fake, lesson_id, stored=root)

    for method in ("upload_bytes", "create_page", "append_block_children",
                   "clear_content_blocks", "delete_block"):
        assert fake.count(method) == 0, f"{method} was called on a conflicted page"
    assert fake.blocks == before
