from unittest.mock import MagicMock, patch
import pytest
from app.services.notion.client import NotionClientWrapper


def _wrapper():
    with patch("app.services.notion.client.Client") as sdk:
        w = NotionClientWrapper(api_key="ntn_test")
        w._min_interval = 0.0  # no sleeping in tests
        return w, sdk.return_value


def test_rejects_bad_key():
    with pytest.raises(ValueError):
        NotionClientWrapper(api_key="bad_key")


def test_create_page_calls_sdk():
    w, sdk = _wrapper()
    sdk.pages.create.return_value = {"id": "page_1"}
    out = w.create_page("parent_1", "1.1 Burchaklar")
    assert out["id"] == "page_1"
    kwargs = sdk.pages.create.call_args.kwargs
    assert kwargs["parent"] == {"page_id": "parent_1"}
    assert kwargs["properties"]["title"][0]["text"]["content"] == "1.1 Burchaklar"


def test_get_child_pages_filters_child_page_blocks():
    w, sdk = _wrapper()
    sdk.blocks.children.list.return_value = {
        "results": [
            {"id": "a", "type": "child_page", "child_page": {"title": "Homework"}},
            {"id": "b", "type": "paragraph", "paragraph": {}},
        ],
        "has_more": False,
    }
    pages = w.get_child_pages("parent_1")
    assert pages == [{"id": "a", "title": "Homework", "type": "child_page"}]


def test_append_block_children_chunks_at_100():
    w, sdk = _wrapper()
    sdk.blocks.children.append.return_value = {"results": []}
    children = [{"object": "block", "type": "divider", "divider": {}} for _ in range(250)]
    w.append_block_children("page_1", children)
    assert sdk.blocks.children.append.call_count == 3  # 100 + 100 + 50


def test_upload_bytes_two_step(monkeypatch):
    w, _ = _wrapper()

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.text = ""

        def json(self):
            return self._payload

    posts = []

    class _HttpClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            posts.append(url)
            if url.endswith("/file_uploads"):
                return _Resp(200, {"id": "upl_9"})
            return _Resp(200, {"id": "upl_9", "status": "uploaded"})

    monkeypatch.setattr("app.services.notion.client.httpx.Client", _HttpClient)
    upload_id = w.upload_bytes(b"hello", "homework.md", "text/markdown")
    assert upload_id == "upl_9"
    assert posts[0].endswith("/v1/file_uploads")
    assert posts[1].endswith("/v1/file_uploads/upl_9/send")
