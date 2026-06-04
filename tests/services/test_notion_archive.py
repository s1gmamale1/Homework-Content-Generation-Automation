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
    Gamified Practices(container → game children) · Boss Arena · Reflection."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.get_child_pages.return_value = []
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
        "Generated Lessons",
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
    client.get_child_pages.return_value = []
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
    client.get_child_pages.return_value = []
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", False))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="L",
        phase_md={"case-based-preview": "# CBP", "boss-arena": "# B"},
        find_or_create=na_find,
    )
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()


def test_push_adopts_matching_human_page():
    """A unique content-word match writes Homework INSIDE the human lesson page —
    no 'Generated Lessons' container, no lesson find_or_create."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_child_pages.return_value = [
        {"id": "human_lesson", "title": "1-mavzu. German qabilalari va Rim imperiyasi…………………6"}
    ]
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj",
        lesson_title="1 German qabilalari va Rim imperiyasi",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert "Generated Lessons" not in titles
    assert titles[0] == "Homework"
    # Homework was created under the ADOPTED human page, not a new app page
    assert na_find.call_args_list[0].args[1] == "human_lesson"


def test_push_falls_back_to_container_when_no_match():
    """No content-word match → Subject > Generated Lessons > <lesson> > Homework."""
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.side_effect = lambda data, name, ctype: f"upl::{name}"
    client.get_child_pages.return_value = [
        {"id": "a1", "title": "1. Yig'indining kvadrati va ayirmaning kvadrati ....57"}
    ]
    na_find = MagicMock(side_effect=lambda c, parent, title: (f"id::{title}", True))
    na._push_to_notion(
        client=client, subject_page_id="subj", lesson_title="1 Sonli ifodalar",
        phase_md={"case-based-preview": "# CBP"}, find_or_create=na_find,
    )
    titles = [c.args[2] for c in na_find.call_args_list]
    assert titles[:3] == ["Generated Lessons", "1 Sonli ifodalar", "Homework"]
    assert na_find.call_args_list[0].args[1] == "subj"                  # container under subject
    assert na_find.call_args_list[1].args[1] == "id::Generated Lessons"  # lesson under container
