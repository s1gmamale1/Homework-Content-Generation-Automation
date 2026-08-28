"""Unit tests for deck_images.fill_images — stubbed image client."""

import json

import pytest

from app.services import deck_images

DECK = """## 5. Tenglamalar sistemasi

Definition line stays.

```
ELEMENT: image
{"scene": "two crossing paths in a park", "caption": "Bitta yechim", "width": "0.5x"}
```

## 6. Parallel Lines

```
ELEMENT: image
{"scene": "two parallel train tracks", "caption": "Yechim yo'q", "width": "0.5x"}
```

```
ELEMENT: test
{"type": "single_choice", "question": "q?", "options": ["a", "b"], "correct_answers": ["a"]}
```
"""


class _Resp:
    def __init__(self, b64):
        class D:  # noqa: D401
            b64_json = b64
        self.data = [D()]


class _Images:
    def __init__(self, fail_scenes=()):
        self.fail_scenes = fail_scenes
        self.calls = []

    async def generate(self, **kw):
        self.calls.append(kw)
        for f in self.fail_scenes:
            if f in kw["prompt"]:
                raise RuntimeError("boom")
        return _Resp("QUJD")  # "ABC"


class _Client:
    def __init__(self, images):
        self.images = images


@pytest.mark.asyncio
async def test_fills_all_dataless_fences(monkeypatch):
    imgs = _Images()
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(imgs))
    out, made, failed = await deck_images.fill_images(DECK)
    assert (made, failed) == (2, 0)
    assert out.count('"data": "QUJD"') == 2
    # style prefix carried the scene and the owner's style language
    assert any("two crossing paths" in c["prompt"] for c in imgs.calls)
    assert all("flat-vector" in c["prompt"] for c in imgs.calls)
    # the test element is untouched
    assert '"type": "single_choice"' in out


@pytest.mark.asyncio
async def test_failed_scene_strips_whole_fence(monkeypatch):
    imgs = _Images(fail_scenes=("parallel train tracks",))
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(imgs))
    out, made, failed = await deck_images.fill_images(DECK)
    assert (made, failed) == (1, 1)
    assert "parallel train tracks" not in out          # fence removed entirely
    assert out.count("ELEMENT: image") == 1
    assert '"data": "QUJD"' in out


@pytest.mark.asyncio
async def test_no_credential_strips_and_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("CLODEX_API_KEY unset")
    monkeypatch.setattr(deck_images, "_client", _boom)
    out, made, failed = await deck_images.fill_images(DECK)
    assert made == 0 and failed == 2
    assert "ELEMENT: image" not in out                 # data-less fences gone
    assert '"type": "single_choice"' in out            # other elements intact


@pytest.mark.asyncio
async def test_already_filled_fence_untouched(monkeypatch):
    filled = DECK.replace(
        '{"scene": "two crossing paths in a park", "caption": "Bitta yechim", "width": "0.5x"}',
        json.dumps({"scene": "s", "caption": "c", "width": "0.5x", "data": "OLD"}),
    )
    imgs = _Images()
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(imgs))
    out, made, failed = await deck_images.fill_images(filled)
    assert made == 1 and failed == 0                   # only the second fence
    assert '"data": "OLD"' in out
    assert len(imgs.calls) == 1
