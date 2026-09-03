import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

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


def _track_page_content(client):
    """Make a MagicMock client's `page_has_content` model reality: False until
    the page receives a non-empty `append_block_children`, True afterwards —
    so the leaf-integrity gate at the end of `_push_to_notion` sees what a
    real Notion read-back would."""
    def _has(page_id):
        return any(
            c.args[0] == page_id and len(c.args[1]) > 0
            for c in client.append_block_children.call_args_list
        )
    client.page_has_content.side_effect = _has


def test_push_builds_grouped_structure():
    """Homework → Case-Based Preview · Flashcards(=flashcards+memory-check) ·
    Gamified Practices(container → game children) · Boss Arena · Reflection.
    Always routes through the archive container → <lesson> → Homework."""
    client = MagicMock()
    _track_page_content(client)
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
        "Platform Homeworks",
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


def test_push_attaches_one_authoritative_json_manifest_to_homework_page():
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    manifest = {
        "schema": "hcg-notion-envelope@1",
        "source": "hcg",
        "source_ref": "book-1",
        "external_key": "job-1",
        "language": "uz",
        "grade": "8",
        "phases": [],
        "artifact_digest": {
            "algorithm": "sha256",
            "canonicalization": "json-sort-keys-utf8",
            "value": "a" * 64,
        },
    }

    na._push_to_notion(
        client=client,
        subject_page_id="subj",
        lesson_title="L",
        phase_md={"case-based-preview": "# CBP"},
        homework_manifest=manifest,
        find_or_create=MagicMock(side_effect=_fake_find),
    )

    json_uploads = [
        call for call in client.upload_bytes.call_args_list
        if call.args[1] == "homework-envelope.v1.json"
    ]
    assert len(json_uploads) == 1
    data, filename, mime = json_uploads[0].args
    assert filename == "homework-envelope.v1.json"
    assert mime == "application/json"
    assert json.loads(data.decode("utf-8")) == manifest

    homework_writes = [
        call for call in client.append_block_children.call_args_list
        if call.args[0] == "id::Homework"
    ]
    assert len(homework_writes) == 1
    assert homework_writes[0].args[1] == [{
        "object": "block",
        "type": "file",
        "file": {
            "type": "file_upload",
            "file_upload": {"id": "upl::homework-envelope.v1.json"},
            "name": "homework-envelope.v1.json",
        },
    }]


def test_push_replaces_existing_manifest_blocks_without_clearing_other_homework_content():
    client = MagicMock()
    _track_page_content(client)
    client.get_block_children.return_value = [
        {"id": "old-1", "type": "file", "file": {"name": "homework-envelope.v1.json"}},
        {"id": "keep", "type": "paragraph", "paragraph": {}},
        {"id": "old-2", "type": "file", "file": {"name": "homework-envelope.v1.json"}},
    ]
    client.upload_bytes.return_value = "new-upload"

    na._push_to_notion(
        client=client,
        subject_page_id="subj",
        lesson_title="L",
        phase_md={"case-based-preview": "# CBP"},
        homework_manifest={"schema": "hcg-notion-envelope@1"},
        find_or_create=MagicMock(side_effect=_fake_find),
    )

    assert client.delete_block.call_args_list == [
        call("old-1"),
        call("old-2"),
    ]
    client.clear_content_blocks.assert_not_called()


def test_push_preserves_existing_manifests_when_new_manifest_append_fails():
    client = MagicMock()
    client.get_block_children.return_value = [
        {"id": "old-1", "type": "file", "file": {"name": "homework-envelope.v1.json"}},
        {"id": "old-2", "type": "file", "file": {"name": "homework-envelope.v1.json"}},
    ]
    client.upload_bytes.return_value = "new-upload"
    client.append_block_children.side_effect = RuntimeError("append failed")

    with pytest.raises(RuntimeError, match="append failed"):
        na._push_to_notion(
            client=client,
            subject_page_id="subj",
            lesson_title="L",
            phase_md={"case-based-preview": "# CBP"},
            homework_manifest={"schema": "hcg-notion-envelope@1"},
            find_or_create=MagicMock(side_effect=_fake_find),
        )

    client.delete_block.assert_not_called()


def test_flashcards_page_attachments_at_top_then_content():
    client = MagicMock()
    _track_page_content(client)
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
    the archive container → <lesson> → 'Homework'.
    Also verifies get_child_pages is never called (the pre-scan is removed)."""
    client = MagicMock()
    _track_page_content(client)
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
    assert titles[:3] == ["Platform Homeworks", "1-§ x", "Homework"]
    # Container created under subject, not under any human page
    assert na_find.call_args_list[0].args[1] == "subj"
    # get_child_pages should never be called — the pre-scan is removed
    client.get_child_pages.assert_not_called()


def test_push_unconditional_container_path():
    """Subject > Platform Homeworks > <lesson> > Homework — always, no fallback logic."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="1 Sonli ifodalar",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles[:3] == ["Platform Homeworks", "1 Sonli ifodalar", "Homework"]
    assert na_find.call_args_list[0].args[1] == "subj"                     # container under subject
    assert na_find.call_args_list[1].args[1] == "id::Platform Homeworks"  # lesson under container


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
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    assert lesson_id == "id::L"
    assert homework_id == "id::Homework"
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles == ["Platform Homeworks", "L", "Homework", "Case-Based Preview"]


def test_push_to_notion_adopts_passed_lesson_page_id():
    """Passing a lesson_page_id VERIFIED under the current container makes the
    Homework page a child of it directly — find_or_create is never called for
    the lesson title (adoption, no re-key)."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.return_value = "id::Platform Homeworks"  # verified
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
    """REUSE branch, no lesson_page_id: the guard derives the lesson from
    get_page_parent(homework_page_id), then verifies THAT page's parent is the
    current container — two parent lookups, then reuse."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.side_effect = (
        lambda pid: {"HW": "PARENT_LESSON", "PARENT_LESSON": "id::Platform Homeworks"}[pid]
    )
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW",
    )
    assert homework_id == "HW"
    assert lesson_id == "PARENT_LESSON"
    assert [c.args[0] for c in client.get_page_parent.call_args_list] == ["HW", "PARENT_LESSON"]


def test_push_to_notion_reuse_branch_backfill_failure_is_swallowed():
    """If get_page_parent raises, the stored pointer is UNVERIFIED: no
    exception escapes, the stored page gets no write, and the homework files
    fresh under the current container (fail-safe for the legacy freeze)."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.side_effect = RuntimeError("boom")
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW",
    )
    assert homework_id == "id::Homework"
    assert lesson_id == "id::L"


def test_push_to_notion_reuse_branch_prefers_passed_lesson_page_id_over_backfill():
    """A passed lesson_page_id wins over deriving it from the homework page —
    only the verification lookup on the lesson itself is made (one call)."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.return_value = "id::Platform Homeworks"  # verified
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW", lesson_page_id="LID",
    )
    assert lesson_id == "LID"
    assert homework_id == "HW"
    client.get_page_parent.assert_called_once_with("LID")


def test_push_to_notion_reuse_branch_skips_backfill_when_disabled():
    """backfill_lesson_id is VESTIGIAL since the legacy-container guard: the
    verification walk always resolves the lesson id, and it can never be
    skipped — safety beats the saved API call the flag used to buy."""
    client = MagicMock()
    _track_page_content(client)
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_page_parent.side_effect = (
        lambda pid: {"HW": "PARENT_LESSON", "PARENT_LESSON": "id::Platform Homeworks"}[pid]
    )
    na_find = MagicMock(side_effect=_fake_find)
    lesson_id, homework_id = na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
        homework_page_id="HW", backfill_lesson_id=False,
    )
    assert homework_id == "HW"
    assert lesson_id == "PARENT_LESSON"
    assert client.get_page_parent.called


def test_push_raises_when_a_leaf_ends_empty():
    """Leaf-integrity gate (2026-09-02 geometry Teacher Pack incident): a leaf
    whose page ends the push with zero content blocks fails the push — it can
    never ride a 'successful' push into the archived stamp, where the
    already-archived fast-path would hide it forever."""
    client = MagicMock()
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"

    def _has(page_id):
        if page_id == "id::Flashcards":
            return False                      # this leaf's append never lands
        return any(
            c.args[0] == page_id and len(c.args[1]) > 0
            for c in client.append_block_children.call_args_list
        )
    client.page_has_content.side_effect = _has

    with pytest.raises(na.LeafEmptyAfterPushError, match="Flashcards"):
        na._push_to_notion(
            client=client, subject_page_id="subj", lesson_title="L",
            phase_md={"case-based-preview": "# CBP", "flashcards": "# FC"},
            find_or_create=MagicMock(side_effect=_fake_find),
        )


def test_push_fills_previously_created_empty_shell_and_passes_gate():
    """A killed pass leaves find_or_create'd EMPTY shells behind; the next
    complete pass appends to them (page_has_content False → write) and the
    gate then sees content — the hollow-Teacher-Pack shape self-heals on the
    next push instead of persisting."""
    client = MagicMock()
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    _track_page_content(client)   # every page starts empty, like the shells

    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"teacher-pack": "# TP"},
        find_or_create=MagicMock(side_effect=_fake_find),
    )
    appended = [c.args[0] for c in client.append_block_children.call_args_list]
    assert "id::Teacher Pack" in appended


def test_guard_rejects_trashed_lesson_page():
    """2026-09-03 cleanup: a trashed page still has its old parent, so
    parentage alone would verify it — liveness must gate reuse."""
    client = MagicMock()
    client.page_is_live.return_value = False
    assert na._verified_under_container(
        client, "CONT", lesson_page_id="L", homework_page_id="HW") == (None, None)
    client.get_page_parent.assert_not_called()


def test_guard_rejects_trashed_homework_page_even_with_live_lesson():
    client = MagicMock()
    client.page_is_live.side_effect = lambda pid: pid != "HW"
    client.get_page_parent.return_value = "CONT"
    assert na._verified_under_container(
        client, "CONT", lesson_page_id="L", homework_page_id="HW") == (None, None)


def test_guard_accepts_live_pages_under_container():
    client = MagicMock()
    client.page_is_live.return_value = True
    client.get_page_parent.return_value = "CONT"
    assert na._verified_under_container(
        client, "CONT", lesson_page_id="L", homework_page_id="HW") == ("L", "HW")
