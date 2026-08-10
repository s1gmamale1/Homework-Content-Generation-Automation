"""Read-only Notion page classifier for the collision repair's cleanup step.

After `scripts/repair_notion_collisions.py` clears the DB pointers (see
`tests/scripts/test_repair_notion_collisions.py`), a "mixed" homework page may
still carry leaves authored by MORE THAN ONE lesson — a non-owner appended
`practice-*` leaves the owner never produced. Before anything is rewritten or
pruned (a LATER task), the repair needs to read a page's actual leaves and
classify which phases it hosts vs. which are extras belonging to some other
lesson.

`classify_page(client, page_id, owner_phase_set)` is that classifier. It is
STRICTLY read-only — it walks `get_child_pages`/`get_block_children`, never
calls a write method, and never raises: any client error is captured as an
`unreadable` verdict.

No DB, no real Notion, no network — a hand-built `FakeNotionClient` only.

Run:
  uv run python -m pytest tests/scripts/test_repair_notion_refresh.py -q
"""
from __future__ import annotations

from scripts.repair_notion_collisions import PageClassification, classify_page


class FakeNotionClient:
    """Canned `get_child_pages`/`get_block_children` responses — no real API.

    `raise_on` is a set of block/page ids that raise `RuntimeError` from
    EITHER method, simulating a Notion read failure."""

    def __init__(
        self,
        child_pages: dict[str, list[dict]],
        block_children: dict[str, list[dict]],
        raise_on: set[str] | None = None,
    ) -> None:
        self._child_pages = child_pages
        self._block_children = block_children
        self._raise_on = raise_on or set()

    def get_child_pages(self, parent_id: str) -> list[dict]:
        if parent_id in self._raise_on:
            raise RuntimeError(f"fake notion error: get_child_pages({parent_id})")
        return self._child_pages.get(parent_id, [])

    def get_block_children(self, block_id: str) -> list[dict]:
        if block_id in self._raise_on:
            raise RuntimeError(f"fake notion error: get_block_children({block_id})")
        return self._block_children.get(block_id, [])


def _file_block(name: str) -> dict:
    return {"type": "file", "file": {"name": name}}


HW = "hw-page"
CBP_LEAF = "leaf-cbp"
FLASH_LEAF = "leaf-flash"
CONTAINER = "container-gamified"
GAME_RLC = "leaf-game-rlc"
BOSS_LEAF = "leaf-boss"
REFLECT_LEAF = "leaf-reflect"

_BASE_CHILD_PAGES = {
    HW: [
        {"id": CBP_LEAF, "title": "Case-Based Preview", "type": "child_page"},
        {"id": FLASH_LEAF, "title": "Flashcards", "type": "child_page"},
        {"id": CONTAINER, "title": "Gamified Practices", "type": "child_page"},
        {"id": BOSS_LEAF, "title": "Boss Arena", "type": "child_page"},
        {"id": REFLECT_LEAF, "title": "Reflection", "type": "child_page"},
    ],
    CONTAINER: [
        {"id": GAME_RLC, "title": "Real-Life Challenge", "type": "child_page"},
    ],
}

_BASE_BLOCK_CHILDREN = {
    CBP_LEAF: [_file_block("case-based-preview.md")],
    FLASH_LEAF: [_file_block("flashcards.md")],
    GAME_RLC: [_file_block("practice-rlc.md")],
    BOSS_LEAF: [_file_block("boss-arena.md")],
    REFLECT_LEAF: [_file_block("reflection.md")],
}

_OWNER_PHASES = {
    "case-based-preview", "flashcards", "memory-check",
    "practice-rlc", "practice-error-detection", "practice-tictactoe",
    "boss-arena", "reflection",
}  # 8 phases


def test_classify_clean():
    client = FakeNotionClient(_BASE_CHILD_PAGES, _BASE_BLOCK_CHILDREN)

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "clean"
    assert result.page_phases == frozenset(
        {"case-based-preview", "flashcards", "practice-rlc", "boss-arena", "reflection"}
    )
    assert result.extra_phases == frozenset()
    assert result.extra_child_page_ids == ()
    assert result.error is None


def test_classify_mixed_extra_practice_leaves():
    jigsaw_id, memory_match_id, sentence_id = (
        "leaf-game-jigsaw", "leaf-game-memory-match", "leaf-game-sentence",
    )
    child_pages = {
        **_BASE_CHILD_PAGES,
        CONTAINER: [
            *_BASE_CHILD_PAGES[CONTAINER],
            {"id": jigsaw_id, "title": "Jigsaw Matching", "type": "child_page"},
            {"id": memory_match_id, "title": "Memory Matching", "type": "child_page"},
            {"id": sentence_id, "title": "Sentence Filling", "type": "child_page"},
        ],
    }
    block_children = {
        **_BASE_BLOCK_CHILDREN,
        jigsaw_id: [_file_block("practice-jigsaw.md")],
        memory_match_id: [_file_block("practice-memory-match.md")],
        sentence_id: [_file_block("practice-sentence.md")],
    }
    client = FakeNotionClient(child_pages, block_children)

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "mixed"
    assert result.extra_phases == frozenset(
        {"practice-jigsaw", "practice-memory-match", "practice-sentence"}
    )
    assert set(result.extra_child_page_ids) == {jigsaw_id, memory_match_id, sentence_id}
    assert result.error is None
    # the owner's own phases are still part of page_phases, just not "extra"
    assert result.page_phases >= _OWNER_PHASES & result.page_phases


def test_classify_resolves_phase_from_attachment_name():
    """A leaf whose title doesn't match any `PHASE_TITLES` entry still
    resolves correctly from its `{phase}.md` file-block attachment."""
    odd_leaf = "leaf-odd-title"
    child_pages = {HW: [{"id": odd_leaf, "title": "Untitled 7 (copy)", "type": "child_page"}]}
    block_children = {odd_leaf: [_file_block("reflection.md")]}
    client = FakeNotionClient(child_pages, block_children)

    result = classify_page(client, HW, {"reflection"})

    assert result.verdict == "clean"
    assert result.page_phases == frozenset({"reflection"})
    assert result.extra_phases == frozenset()


def test_classify_unreadable_on_client_error():
    client = FakeNotionClient({}, {}, raise_on={HW})

    result = classify_page(client, HW, _OWNER_PHASES)

    assert result.verdict == "unreadable"
    assert result.page_phases == frozenset()
    assert result.extra_phases == frozenset()
    assert result.extra_child_page_ids == ()
    assert result.error is not None
    assert "fake notion error" in result.error


def test_page_classification_is_frozen_dataclass():
    """Sanity: the dataclass is immutable, matching the read-only contract."""
    pc = PageClassification(
        verdict="clean", page_phases=frozenset(), extra_phases=frozenset(),
        extra_child_page_ids=(), error=None,
    )
    import dataclasses

    assert dataclasses.is_dataclass(pc)
    try:
        pc.verdict = "mixed"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("PageClassification must be frozen")
