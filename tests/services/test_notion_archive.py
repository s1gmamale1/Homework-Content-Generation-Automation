import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import app.services.notion_archive as na


def test_resolve_subject_page_id_uses_subject_grade_key():
    mapping = {"geometriya-g7-11|8": "page_geo_8"}
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8") == "page_geo_8"
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", None) is None
    assert na._resolve_subject_page_id(mapping, "biology-g7-11", "8") is None


def test_resolve_subject_page_id_dict_matches_by_filename_keyword():
    mapping = {"history|8": {"jahon": "page_jahon", "ozbekiston": "page_ozbek"}}
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "8-sinf_Jahon_tarixi_2024_(elekton_darslikbot).pdf"
    ) == "page_jahon"
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "8-sinf_Ozbekiston_tarixi_2023_(elekton_darslikbot).pdf"
    ) == "page_ozbek"
    # apostrophe / diacritic variants fold to the same bare keyword
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "9-sinf_O‘zbekiston_tarixi.pdf"
    ) == "page_ozbek"


def test_resolve_subject_page_id_dict_no_keyword_match_returns_none():
    mapping = {"history|8": {"jahon": "page_jahon", "ozbekiston": "page_ozbek"}}
    assert na._resolve_subject_page_id(
        mapping, "history", "8", "8-sinf_Qandaydir_Kitob.pdf"
    ) is None


def test_resolve_subject_page_id_string_value_ignores_hint():
    # A grade with a single combined history page → plain string, hint irrelevant.
    mapping = {"history|5": "combined_tarix_page"}
    assert na._resolve_subject_page_id(
        mapping, "history", "5", "5-sinf_Tarix.pdf"
    ) == "combined_tarix_page"


def test_lesson_title_from_section():
    assert na._lesson_title("1.1", "Burchaklar") == "1.1 Burchaklar"
    assert na._lesson_title(None, "Kirish") == "Kirish"


def _fake_find(c, parent, title):
    # find_or_create(client, parent_id, title) -> (page_id, created)
    return (f"id::{title}", True)


def test_push_builds_grouped_structure():
    """Homework → Case-Based Preview · Flashcards(=flashcards+memory-check) ·
    Gamified Practices(container → game children) · Boss Arena · Reflection.
    Always routes through Generated Homeworks → <lesson> → Homework."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    phase_md = {
        "case-based-preview": "# CBP",
        "flashcards": "# FC",
        "memory-check": "# MC",
        "practice-rlc": "# RLC",
        "practice-error-detection": "# ED",
        "practice-tictactoe": "# TTT",   # this job's subject game
        "boss-arena": "# BOSS",
        "reflection": "# REF",
    }
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="1-§ x",
        phase_md=phase_md, find_or_create=na_find,
    )
    titles = [call.args[2] for call in na_find.call_args_list]
    assert titles == [
        "Generated Homeworks",
        "1-§ x", "Homework",
        "Case-Based Preview",
        "Flashcards",
        "Gamified Practices",
        "Real-Life Challenge", "Error Detection", "TicTacToe",
        "Boss Arena",
        "Reflection",
    ]
    # one upload per phase (all 8 present)
    assert client.upload_bytes.call_count == 8
    # content written to 7 leaf pages; the Gamified Practices container gets none
    assert client.append_block_children.call_count == 7


def test_flashcards_page_attachments_at_top_then_content():
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"flashcards": "# FC\n\nbody", "memory-check": "# MC\n\nbody"},
        find_or_create=na_find,
    )
    fc_call = next(
        c for c in client.append_block_children.call_args_list if c.args[0] == "id::Flashcards"
    )
    body = fc_call.args[1]
    # both attachments sit at the very top of the page
    assert body[0]["type"] == "file"
    assert body[1]["type"] == "file"
    assert client.upload_bytes.call_count == 2  # one .md per phase, both on this page


def test_push_skips_pages_already_populated():
    client = MagicMock()
    client.page_has_content.return_value = True   # already populated → skip writes
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP", "boss-arena": "# B"},
        find_or_create=na_find,
    )
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()


def test_push_ignores_matching_human_child_always_routes_via_container():
    """Adoption is GONE. Even when the subject page already has a child whose title
    equals the lesson title, the archive still routes unconditionally through
    'Generated Homeworks' → <lesson> → 'Homework'.
    Also verifies get_child_pages is never called (the pre-scan is removed)."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    # A child page whose title exactly matches the lesson title would have been adopted
    # under the old behavior — it must be ignored now.
    client.get_child_pages.return_value = [
        {"id": "human_lesson", "title": "1-§ x"}
    ]
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj",
        lesson_title="1-§ x",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    # Unconditional path: Generated Homeworks → lesson → Homework
    assert titles[:3] == ["Generated Homeworks", "1-§ x", "Homework"]
    # Container created under subject, not under any human page
    assert na_find.call_args_list[0].args[1] == "subj"
    # get_child_pages should never be called — the pre-scan is removed
    client.get_child_pages.assert_not_called()


def test_push_unconditional_container_path():
    """Subject > Generated Homeworks > <lesson> > Homework — always, no fallback logic."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="1 Sonli ifodalar",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles[:3] == ["Generated Homeworks", "1 Sonli ifodalar", "Homework"]
    assert na_find.call_args_list[0].args[1] == "subj"                     # container under subject
    assert na_find.call_args_list[1].args[1] == "id::Generated Homeworks"  # lesson under container


def test_push_replace_clears_then_rewrites_populated_page():
    client = MagicMock()
    client.page_has_content.return_value = True      # page already populated
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        replace=True,
    )
    # replace=True → the stale content is cleared, then the fresh content written
    client.clear_content_blocks.assert_called_once_with("id::Case-Based Preview")
    client.append_block_children.assert_called_once()
    assert client.append_block_children.call_args.args[0] == "id::Case-Based Preview"


def test_push_replace_false_still_skips_populated_page():
    # Control: default (replace=False) must NOT clear — pure idempotent skip.
    client = MagicMock()
    client.page_has_content.return_value = True
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    client.clear_content_blocks.assert_not_called()
    client.append_block_children.assert_not_called()


# --- Task 4: _push_to_notion returns (lesson_id, homework_id) ----------------


def test_push_to_notion_returns_lesson_and_homework_ids():
    """CREATE branch: both ids in the returned tuple come from find_or_create,
    and the leaf-page titles/order are unaffected by the return-type change
    (regression guard for the byte-identical homework pages requirement)."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    assert lesson_id == "id::L"
    assert homework_id == "id::Homework"
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles == ["Generated Homeworks", "L", "Homework", "Case-Based Preview"]


def test_push_to_notion_adopts_passed_lesson_page_id():
    """Passing lesson_page_id makes the Homework page a child of it directly —
    find_or_create is never called for the lesson title (adoption, no re-key)."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        lesson_page_id="LID",
    )
    assert lesson_id == "LID"
    homework_call = next(c for c in na_find.call_args_list if c.args[2] == "Homework")
    assert homework_call.args[1] == "LID"  # Homework created directly under the adopted lesson
    titles = [c.args[2] for c in na_find.call_args_list]
    assert "L" not in titles  # lesson title never looked up


def test_push_to_notion_reuse_branch_backfills_lesson_id_from_parent():
    """REUSE branch, no lesson_page_id: get_page_parent(homework_page_id)
    backfills lesson_id from the Homework sub-page's actual parent."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.return_value = "PARENT_LESSON"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW",
    )
    assert homework_id == "HW"
    assert lesson_id == "PARENT_LESSON"
    client.get_page_parent.assert_called_once_with("HW")


def test_push_to_notion_reuse_branch_backfill_failure_is_swallowed():
    """If get_page_parent raises, lesson_id is None and no exception escapes —
    the stamp is simply skipped this run and self-heals on the next archive."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.side_effect = RuntimeError("boom")
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW",
    )
    assert homework_id == "HW"
    assert lesson_id is None


def test_push_to_notion_reuse_branch_prefers_passed_lesson_page_id_over_backfill():
    """A passed lesson_page_id wins over the get_page_parent backfill — no
    need to ask Notion when the caller already knows the lesson page."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW", lesson_page_id="LID",
    )
    assert lesson_id == "LID"
    assert homework_id == "HW"
    client.get_page_parent.assert_not_called()


def test_push_to_notion_reuse_branch_skips_backfill_when_disabled():
    """backfill_lesson_id=False must not call get_page_parent — used by the
    repair sweep, which discards lesson_id and would otherwise waste a
    rate-limited Notion API call for nothing."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW", backfill_lesson_id=False,
    )
    assert homework_id == "HW"
    assert lesson_id is None
    client.get_page_parent.assert_not_called()


# --- Task 3: V1 renderer parity lock (golden transcript) --------------------
#
# The versioned-homework writer (`app/services/notion_versioned_homework.py`)
# reuses V1's layout walk, which means that walk had to be lifted out of
# `_push_to_notion` into a module-level helper. This test is the lock that the
# extraction was behavior-preserving: it drives the REAL `_push_to_notion` (real
# `find_or_create`, real block builders) with a full 11-phase `phase_md` through
# a recording fake and freezes the exact ordered sequence of client calls and
# their arguments — page titles, parents, upload names, and a content digest of
# every appended block list.
#
# The golden below was captured from the PRE-refactor implementation and is
# never regenerated: if a future change alters the Notion-block bytes V1 writes,
# this test is supposed to go red.

_GOLDEN_PHASE_MD: dict[str, str] = {
    # All 11 content phases (`flows._BASE_PHASES` + games + boss-arena +
    # reflection), each with markdown exercising a different converter branch:
    # headings, bullets, inline bold/italic, `---` dividers, image placeholders.
    "case-based-preview": "# Case\n\nA **bold** claim and *italic* aside.\n\n---\n\nTail line.",
    "flashcards": "## Cards\n\n- front / back\n- 3 * 4 = 12\n",
    "memory-check": "### Check\n\nOne line.\n\nAnother paragraph\nwrapped over two lines.",
    "practice-rlc": "# RLC\n\n![a described diagram](placeholder)\n\nDo the thing.",
    "practice-error-detection": "# ED\n\n- spot the ***error***\n",
    "practice-memory-match": "# MM\n\npairs",
    "practice-tictactoe": "# TTT\n\ngrid",
    "practice-jigsaw": "# Jigsaw\n\n* piece one\n* piece two",
    "practice-sentence": "# Sentence\n\nfill ___ in",
    "boss-arena": "# Boss\n\nQ1. Answer?\n\n---\n\nQ2. Answer?",
    "reflection": "# Reflection\n\nWhat stuck?",
}


def _digest(payload: object) -> str:
    """Short stable digest of a JSON-serializable payload (block lists, bytes)."""
    if isinstance(payload, (bytes, bytearray)):
        raw = bytes(payload)
    else:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


class _RecordingNotion:
    """Fake `NotionClientWrapper` that records every call, in order, as a
    human-diffable string. Models Notion's 'a page keeps its content' behaviour
    so `page_has_content` answers truthfully during the walk."""

    def __init__(self) -> None:
        self.transcript: list[str] = []
        self.pages: dict[str, dict] = {}     # id -> {"title", "parent"}
        self.content: dict[str, list] = {}   # id -> appended blocks
        self._n = 0

    def get_child_pages(self, parent_id: str) -> list[dict]:
        self.transcript.append(f"get_child_pages parent={parent_id}")
        return [{"id": pid, "title": p["title"]}
                for pid, p in self.pages.items() if p["parent"] == parent_id]

    def create_page(self, parent_id: str, title: str, children=None) -> dict:
        self._n += 1
        pid = f"pg{self._n}"
        self.pages[pid] = {"title": title, "parent": parent_id}
        kids = "none" if not children else f"n={len(children)} sha={_digest(children)}"
        self.transcript.append(
            f"create_page parent={parent_id} title={title!r} id={pid} children={kids}")
        return {"id": pid}

    def page_has_content(self, page_id: str) -> bool:
        has = bool(self.content.get(page_id))
        self.transcript.append(f"page_has_content id={page_id} -> {has}")
        return has

    def append_block_children(self, block_id: str, children: list) -> dict:
        self.transcript.append(
            f"append_block_children id={block_id} n={len(children)} "
            f"types={[b['type'] for b in children]} sha={_digest(children)}")
        self.content.setdefault(block_id, []).extend(children)
        return {"results": []}

    def clear_content_blocks(self, page_id: str) -> int:
        n = len(self.content.pop(page_id, []))
        self.transcript.append(f"clear_content_blocks id={page_id} deleted={n}")
        return n

    def delete_block(self, block_id: str) -> None:
        self.transcript.append(f"delete_block id={block_id}")

    def upload_bytes(self, data: bytes, file_name: str, content_type: str) -> str:
        self.transcript.append(
            f"upload_bytes name={file_name!r} type={content_type} sha={_digest(data)}")
        return f"upl::{file_name}"

    def get_page_parent(self, page_id: str):
        self.transcript.append(f"get_page_parent id={page_id}")
        return self.pages.get(page_id, {}).get("parent")


_V1_GOLDEN_TRANSCRIPT: list[str] = [
    'get_child_pages parent=subj',
    "create_page parent=subj title='Generated Homeworks' id=pg1 children=none",
    'get_child_pages parent=pg1',
    "create_page parent=pg1 title='1-§ Sonli ifodalar' id=pg2 children=none",
    'get_child_pages parent=pg2',
    "create_page parent=pg2 title='Homework' id=pg3 children=none",
    'get_child_pages parent=pg3',
    "create_page parent=pg3 title='Case-Based Preview' id=pg4 children=none",
    'page_has_content id=pg4 -> False',
    "upload_bytes name='case-based-preview.md' type=text/markdown sha=8421f291a783",
    "append_block_children id=pg4 n=6 types=['file', 'divider', 'heading_1', 'paragraph', 'divider', 'paragraph'] sha=e81b7fe094e7",
    'get_child_pages parent=pg3',
    "create_page parent=pg3 title='Flashcards' id=pg5 children=none",
    'page_has_content id=pg5 -> False',
    "upload_bytes name='flashcards.md' type=text/markdown sha=6521a7711b59",
    "upload_bytes name='memory-check.md' type=text/markdown sha=475ea021eadd",
    "append_block_children id=pg5 n=10 types=['file', 'file', 'divider', 'heading_2', 'bulleted_list_item', 'bulleted_list_item', 'divider', 'heading_3', 'paragraph', 'paragraph'] sha=2107c13977cf",
    'get_child_pages parent=pg3',
    "create_page parent=pg3 title='Gamified Practices' id=pg6 children=none",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='Real-Life Challenge' id=pg7 children=none",
    'page_has_content id=pg7 -> False',
    "upload_bytes name='practice-rlc.md' type=text/markdown sha=1068885ee329",
    "append_block_children id=pg7 n=5 types=['file', 'divider', 'heading_1', 'callout', 'paragraph'] sha=2769c749ca9b",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='Error Detection' id=pg8 children=none",
    'page_has_content id=pg8 -> False',
    "upload_bytes name='practice-error-detection.md' type=text/markdown sha=afdbbebe65db",
    "append_block_children id=pg8 n=4 types=['file', 'divider', 'heading_1', 'bulleted_list_item'] sha=b66b78cffc85",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='Memory Matching' id=pg9 children=none",
    'page_has_content id=pg9 -> False',
    "upload_bytes name='practice-memory-match.md' type=text/markdown sha=78aa5b061c2e",
    "append_block_children id=pg9 n=4 types=['file', 'divider', 'heading_1', 'paragraph'] sha=15539d4e41e9",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='TicTacToe' id=pg10 children=none",
    'page_has_content id=pg10 -> False',
    "upload_bytes name='practice-tictactoe.md' type=text/markdown sha=9a0a42a56423",
    "append_block_children id=pg10 n=4 types=['file', 'divider', 'heading_1', 'paragraph'] sha=419fc3ea9953",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='Jigsaw Matching' id=pg11 children=none",
    'page_has_content id=pg11 -> False',
    "upload_bytes name='practice-jigsaw.md' type=text/markdown sha=493f0fd87bcf",
    "append_block_children id=pg11 n=5 types=['file', 'divider', 'heading_1', 'bulleted_list_item', 'bulleted_list_item'] sha=bcd4348b019f",
    'get_child_pages parent=pg6',
    "create_page parent=pg6 title='Sentence Filling' id=pg12 children=none",
    'page_has_content id=pg12 -> False',
    "upload_bytes name='practice-sentence.md' type=text/markdown sha=4811ae1ee6c2",
    "append_block_children id=pg12 n=4 types=['file', 'divider', 'heading_1', 'paragraph'] sha=7df08784b2ae",
    'get_child_pages parent=pg3',
    "create_page parent=pg3 title='Boss Arena' id=pg13 children=none",
    'page_has_content id=pg13 -> False',
    "upload_bytes name='boss-arena.md' type=text/markdown sha=e882ac32ef2f",
    "append_block_children id=pg13 n=6 types=['file', 'divider', 'heading_1', 'paragraph', 'divider', 'paragraph'] sha=a8e9b8467eed",
    'get_child_pages parent=pg3',
    "create_page parent=pg3 title='Reflection' id=pg14 children=none",
    'page_has_content id=pg14 -> False',
    "upload_bytes name='reflection.md' type=text/markdown sha=472f71113b19",
    "append_block_children id=pg14 n=4 types=['file', 'divider', 'heading_1', 'paragraph'] sha=8a3a5a4bf72e",
]


def test_v1_push_golden_transcript_is_byte_stable():
    """Parity lock: the exact ordered Notion client calls V1 makes for a full
    11-phase homework, including the block bytes of every write."""
    client = _RecordingNotion()
    lesson_id, homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subj",
        lesson_title="1-§ Sonli ifodalar",
        phase_md=_GOLDEN_PHASE_MD,
    )
    assert client.transcript == _V1_GOLDEN_TRANSCRIPT
    assert (lesson_id, homework_id) == ("pg2", "pg3")
