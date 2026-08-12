"""Task 5 — `_push_teacher_deck_to_notion` / `_push_teacher_with_retry` /
`_teacher_deck_blocks`: the Teacher Deck sibling page under a lesson.

Mirrors the shape of `_push_to_notion` / `_leaf_blocks` / `_push_with_retry`
(see `test_notion_lesson_collision.py` for the fake-client pattern this
module borrows), but drives the real functions with a `MagicMock` client and
an injected `find_or_create` returning distinct ids per title.

Two load-bearing behaviors under test:
  - `_teacher_deck_blocks`: ONLY the PDF *render* is inside the try/except —
    a render failure (missing native libs) degrades to a page-only write and
    `upload_bytes` must never be called. An `upload_bytes` failure must
    PROPAGATE (it's a transient-network case the retry wrapper exists for).
  - `_push_teacher_deck_to_notion`: the body (render+upload) is built BEFORE
    `clear_content_blocks`, so a render/upload failure on a force re-archive
    can never empty an already-populated page.
"""
import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

import app.services.notion_archive as na
from app.schemas.content_json import TeacherDeck

FIXTURE_PATH = "tests/fixtures/teacher_deck/hindiston_topic19.json"


def _deck() -> TeacherDeck:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return TeacherDeck.model_validate(json.load(fh))


def _fake_find_or_create():
    """Returns distinct (id, created) pairs per (parent_id, title) — mirrors
    the fake-client pattern in test_notion_lesson_collision.py. Records every
    call as `.calls` so tests can assert which titles were (not) looked up."""
    seen: dict[tuple[str, str], str] = {}
    counter = {"n": 0}
    calls: list[tuple[str, str]] = []

    def _foc(client, parent_id, title):
        calls.append((parent_id, title))
        key = (parent_id, title)
        if key not in seen:
            counter["n"] += 1
            seen[key] = f"pg{counter['n']}"
        return seen[key], True

    _foc.calls = calls
    return _foc


def _mock_client(*, populated=False):
    client = MagicMock()
    client.page_has_content.return_value = populated
    client.upload_bytes.return_value = "upload-xyz"
    return client


# ---------------------------------------------------------------------------
# _teacher_deck_blocks
# ---------------------------------------------------------------------------

def test_teacher_deck_blocks_degrade_on_render_failure_never_uploads():
    deck = _deck()
    client = _mock_client()
    with patch.object(na, "render_teacher_deck_pdf", side_effect=OSError("no pango")):
        out = na._teacher_deck_blocks(client, deck)
    client.upload_bytes.assert_not_called()
    assert out  # page-only content still produced
    assert not any(
        b.get("type") == "file" for b in out
    ), "degrade path must not include a file-upload block"


def test_teacher_deck_blocks_uploads_pdf_with_expected_filename_and_content_type():
    deck = _deck()
    client = _mock_client()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        out = na._teacher_deck_blocks(client, deck)
    client.upload_bytes.assert_called_once()
    args, kwargs = client.upload_bytes.call_args
    data, fname, content_type = args
    assert data == b"%PDF-stub"
    assert fname.endswith("— dars ishlanma.pdf")
    assert content_type == "application/pdf"
    assert out[0]["type"] == "file"  # file-upload block leads


def test_teacher_deck_blocks_upload_error_propagates():
    deck = _deck()
    client = _mock_client()
    client.upload_bytes.side_effect = RuntimeError("notion 429")
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        with pytest.raises(RuntimeError):
            na._teacher_deck_blocks(client, deck)


# ---------------------------------------------------------------------------
# _push_teacher_deck_to_notion
# ---------------------------------------------------------------------------

def test_create_adopt_chain_writes_upload_and_markdown_blocks():
    deck = _deck()
    client = _mock_client(populated=False)
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        lesson_id, deck_id = na._push_teacher_deck_to_notion(
            client=client, subject_page_id="S", lesson_title="L", deck=deck,
            find_or_create=foc,
        )
    assert lesson_id is not None
    assert deck_id
    titles_called = [title for (_parent, title) in foc.calls]
    assert titles_called == ["Generated Homeworks", "L", "Teacher Deck"]
    client.append_block_children.assert_called_once()
    call_deck_id, body = client.append_block_children.call_args[0]
    assert call_deck_id == deck_id
    assert body[0]["type"] == "file"
    client.upload_bytes.assert_called_once()
    args, _ = client.upload_bytes.call_args
    assert args[1].endswith("— dars ishlanma.pdf")
    assert args[2] == "application/pdf"


def test_idempotent_skip_when_already_populated():
    deck = _deck()
    client = _mock_client(populated=True)
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        lesson_id, deck_id = na._push_teacher_deck_to_notion(
            client=client, subject_page_id="S", lesson_title="L", deck=deck,
            find_or_create=foc, replace=False,
        )
    assert deck_id
    client.append_block_children.assert_not_called()
    client.clear_content_blocks.assert_not_called()


def test_replace_clears_then_appends():
    deck = _deck()
    client = _mock_client(populated=True)
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        na._push_teacher_deck_to_notion(
            client=client, subject_page_id="S", lesson_title="L", deck=deck,
            find_or_create=foc, replace=True,
        )
    client.clear_content_blocks.assert_called_once()
    client.append_block_children.assert_called_once()


def test_adoption_skips_lesson_find_or_create_when_lesson_page_id_given():
    deck = _deck()
    client = _mock_client(populated=False)
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        lesson_id, deck_id = na._push_teacher_deck_to_notion(
            client=client, subject_page_id="S", lesson_title="L", deck=deck,
            find_or_create=foc, lesson_page_id="LID",
        )
    assert lesson_id == "LID"
    titles_called = [title for (_parent, title) in foc.calls]
    assert "L" not in titles_called
    assert "Teacher Deck" in titles_called
    assert "Generated Homeworks" in titles_called


def test_degrade_render_failure_still_writes_page_no_upload_no_exception():
    deck = _deck()
    client = _mock_client(populated=False)
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", side_effect=OSError("no pango")):
        lesson_id, deck_id = na._push_teacher_deck_to_notion(
            client=client, subject_page_id="S", lesson_title="L", deck=deck,
            find_or_create=foc,
        )
    assert deck_id
    client.append_block_children.assert_called_once()
    client.upload_bytes.assert_not_called()


def test_propagation_upload_failure_and_clear_not_called_before_failure():
    deck = _deck()
    client = _mock_client(populated=True)
    client.upload_bytes.side_effect = RuntimeError("notion 429")
    foc = _fake_find_or_create()
    with patch.object(na, "render_teacher_deck_pdf", return_value=b"%PDF-stub"):
        with pytest.raises(RuntimeError):
            na._push_teacher_deck_to_notion(
                client=client, subject_page_id="S", lesson_title="L", deck=deck,
                find_or_create=foc, replace=True,
            )
    client.clear_content_blocks.assert_not_called()
    client.append_block_children.assert_not_called()


# ---------------------------------------------------------------------------
# _push_teacher_with_retry
# ---------------------------------------------------------------------------

def test_retry_wrapper_returns_tuple_on_success():
    async def _run():
        with patch.object(na, "_push_teacher_deck_to_notion", return_value=("lid", "did")) as fn:
            result = await na._push_teacher_with_retry(
                client=MagicMock(), subject_page_id="S", lesson_title="L", deck=_deck(),
            )
        assert result == ("lid", "did")
        fn.assert_called_once()

    asyncio.run(_run())


def test_retry_wrapper_retries_transient_failure_then_succeeds():
    async def _run():
        calls = {"n": 0}

        def _flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return ("lid", "did")

        with patch.object(na, "_push_teacher_deck_to_notion", side_effect=_flaky):
            with patch("app.services.notion_archive.asyncio.sleep", new=_fast_sleep):
                result = await na._push_teacher_with_retry(
                    client=MagicMock(), subject_page_id="S", lesson_title="L", deck=_deck(),
                )
        assert result == ("lid", "did")
        assert calls["n"] == 2

    asyncio.run(_run())


async def _fast_sleep(_seconds):
    return None
