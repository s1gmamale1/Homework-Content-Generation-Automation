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


def test_push_creates_one_subpage_per_phase():
    client = MagicMock()
    client.page_has_content.return_value = False  # each new subpage empty
    client.upload_bytes.return_value = "upl_x"
    # find_or_create returns (page_id, created) — lesson + Homework + 2 phase subpages
    na_find = MagicMock(side_effect=[("lesson_1", True), ("hw_1", True), ("p_cbp", True), ("p_fc", True)])
    phases = [
        ("Case-Based Preview", "case-based-preview", "# Case\n\nbody"),
        ("Flashcards", "flashcards", "# Flashcards\n\nbody"),
    ]
    na._push_to_notion(
        client=client, subject_page_id="subj_1", lesson_title="1.1 Burchaklar",
        phases=phases, find_or_create=na_find,
    )
    assert na_find.call_count == 4            # lesson + Homework + 2 phase subpages
    assert client.upload_bytes.call_count == 2
    assert client.append_block_children.call_count == 2


def test_push_skips_phase_subpage_already_populated():
    client = MagicMock()
    client.page_has_content.return_value = True   # already populated → skip writes
    na_find = MagicMock(side_effect=[("lesson_1", False), ("hw_1", False), ("p_cbp", False)])
    na._push_to_notion(
        client=client, subject_page_id="subj_1", lesson_title="1.1",
        phases=[("Case-Based Preview", "case-based-preview", "# Case")],
        find_or_create=na_find,
    )
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()
