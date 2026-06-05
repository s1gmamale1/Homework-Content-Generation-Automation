from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app
from app.auth import get_current_user

app.dependency_overrides[get_current_user] = lambda: {"user": "test"}
client = TestClient(app)


def test_grades_endpoint():
    with patch("app.api.v1.notion.NotionClientWrapper"), \
         patch("app.api.v1.notion.notion_fetch.list_grades",
               return_value=[{"title": "9 Grade", "page_id": "g9"}]):
        r = client.get("/api/v1/notion/grades")
    assert r.status_code == 200
    assert r.json()[0]["page_id"] == "g9"


def test_subjects_endpoint():
    rows = [{"notion_title": "Algebra", "page_id": "alg",
             "app_subject": "math-algebra", "has_textbook": True}]
    with patch("app.api.v1.notion.NotionClientWrapper"), \
         patch("app.api.v1.notion.notion_fetch.list_subjects", return_value=rows):
        r = client.get("/api/v1/notion/grades/g9/subjects")
    assert r.status_code == 200
    assert r.json()[0]["app_subject"] == "math-algebra"
