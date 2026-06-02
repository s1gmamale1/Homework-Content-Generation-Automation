from types import SimpleNamespace
from unittest.mock import MagicMock
import app.services.notion_archive as na


def test_resolve_subject_page_id_uses_subject_grade_key():
    mapping = {"geometriya-g7-11|8": "page_geo_8"}
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", "8") == "page_geo_8"
    assert na._resolve_subject_page_id(mapping, "geometriya-g7-11", None) is None
    assert na._resolve_subject_page_id(mapping, "biology-g7-11", "8") is None


def test_lesson_title_from_section():
    assert na._lesson_title("1.1", "Burchaklar") == "1.1 Burchaklar"
    assert na._lesson_title(None, "Kirish") == "Kirish"


def test_push_skips_write_when_page_already_populated():
    client = MagicMock()
    client.page_has_content.return_value = True  # already populated
    na_find = MagicMock(return_value=("hw_1", False))
    homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subj_1",
        lesson_title="1.1 Burchaklar",
        assembled_md="# hw",
        content_json_bytes=b"{}",
        find_or_create=na_find,
    )
    assert homework_id == "hw_1"
    client.append_block_children.assert_not_called()
    client.upload_bytes.assert_not_called()


def test_push_writes_blocks_and_attachments_when_empty():
    client = MagicMock()
    client.page_has_content.return_value = False
    client.upload_bytes.return_value = "upl_x"
    na_find = MagicMock(side_effect=[("lesson_1", True), ("hw_1", True)])
    homework_id = na._push_to_notion(
        client=client,
        subject_page_id="subj_1",
        lesson_title="1.1 Burchaklar",
        assembled_md="# Heading\n\nbody",
        content_json_bytes=b"{}",
        find_or_create=na_find,
    )
    assert homework_id == "hw_1"
    assert client.upload_bytes.call_count == 2  # homework.md + content.json
    client.append_block_children.assert_called_once()
