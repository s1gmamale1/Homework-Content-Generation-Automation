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
