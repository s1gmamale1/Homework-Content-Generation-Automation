from unittest.mock import MagicMock
from app.services.notion.page_creator import find_or_create


def test_returns_existing_when_title_matches_normalized():
    client = MagicMock()
    client.get_child_pages.return_value = [{"id": "h1", "title": "Homework (2)", "type": "child_page"}]
    page_id, created = find_or_create(client, "lesson_1", "Homework")
    assert page_id == "h1"
    assert created is False
    client.create_page.assert_not_called()


def test_creates_when_missing():
    client = MagicMock()
    client.get_child_pages.return_value = []
    client.create_page.return_value = {"id": "new_1"}
    page_id, created = find_or_create(client, "subject_1", "1.1 Burchaklar")
    assert page_id == "new_1"
    assert created is True
    client.create_page.assert_called_once_with("subject_1", "1.1 Burchaklar")
