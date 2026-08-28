"""Unit tests for deck_images.fill_images — stubbed Gemini image client."""

import base64
import io
import json

import pytest
from PIL import Image

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


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 220, 240)).save(buf, format="PNG")
    return buf.getvalue()


def _resp(data: bytes):
    class Blob:
        mime_type = "image/png"
    Blob.data = data

    class Part:
        inline_data = Blob()

    class Content:
        parts = [Part()]

    class Cand:
        content = Content()

    class Resp:
        candidates = [Cand()]
    return Resp()


class _Models:
    def __init__(self, fail_scenes=(), data=None):
        self.fail_scenes = fail_scenes
        self.data = data if data is not None else _tiny_png()
        self.calls = []

    async def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "prompt": contents})
        for f in self.fail_scenes:
            if f in contents:
                raise RuntimeError("boom")
        return _resp(self.data)


class _Client:
    def __init__(self, models):
        class AIO:  # noqa: D401
            pass
        self.aio = AIO()
        self.aio.models = models


@pytest.mark.asyncio
async def test_fills_all_dataless_fences_as_jpeg(monkeypatch):
    models = _Models()
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(models))
    out, made, failed = await deck_images.fill_images(DECK)
    assert (made, failed) == (2, 0)
    # every injected payload decodes to JPEG (recompressed from the PNG)
    for m in deck_images._IMG_FENCE_RE.finditer(out):
        obj = json.loads(m.group(3))
        raw = base64.b64decode(obj["data"])
        assert raw[:3] == b"\xff\xd8\xff"
    # style prefix carried the scene and the owner's style language
    assert any("two crossing paths" in c["prompt"] for c in models.calls)
    assert all("flat-vector" in c["prompt"] for c in models.calls)
    # VERDICT-v186 fixes: no 'logistics' wording; hard no-text clause always on
    assert all("logistics illustration style" not in c["prompt"] for c in models.calls)
    assert all("writing of ANY" in c["prompt"] for c in models.calls)
    assert all(c["model"] == "gemini-2.5-flash-image" for c in models.calls)
    assert '"type": "single_choice"' in out


def test_build_prompt_sanitizes_and_hardens():
    p = deck_images._build_prompt("a chalkboard showing 100 / 2 = 50 packages")
    assert not any(ch in p.split("in my flat-vector")[0] for ch in "0123456789=/")
    assert "completely BLANK" in p          # risky scene → extra-hard variant
    p2 = deck_images._build_prompt("two friendly characters shaking hands where two paths meet")
    assert "completely BLANK" not in p2     # clean metaphor → standard clause
    assert "writing of ANY" in p2
    assert "logistics imagery" in p2        # subject-leak ban always present


@pytest.mark.asyncio
async def test_failed_scene_strips_whole_fence(monkeypatch):
    models = _Models(fail_scenes=("parallel train tracks",))
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(models))
    out, made, failed = await deck_images.fill_images(DECK)
    assert (made, failed) == (1, 1)
    assert "parallel train tracks" not in out
    assert out.count("ELEMENT: image") == 1


@pytest.mark.asyncio
async def test_no_credential_strips_and_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("gemini api: no GEMINI_API_KEY and no Vertex SA")
    monkeypatch.setattr(deck_images, "_client", _boom)
    out, made, failed = await deck_images.fill_images(DECK)
    assert made == 0 and failed == 2
    assert "ELEMENT: image" not in out
    assert '"type": "single_choice"' in out


@pytest.mark.asyncio
async def test_unrecompressible_bytes_fall_back_to_original(monkeypatch):
    models = _Models(data=b"not-an-image")
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(models))
    out, made, failed = await deck_images.fill_images(DECK)
    assert made == 2
    obj = json.loads(next(deck_images._IMG_FENCE_RE.finditer(out)).group(3))
    assert base64.b64decode(obj["data"]) == b"not-an-image"


@pytest.mark.asyncio
async def test_already_filled_fence_untouched(monkeypatch):
    filled = DECK.replace(
        '{"scene": "two crossing paths in a park", "caption": "Bitta yechim", "width": "0.5x"}',
        json.dumps({"scene": "s", "caption": "c", "width": "0.5x", "data": "OLD"}),
    )
    models = _Models()
    monkeypatch.setattr(deck_images, "_client", lambda: _Client(models))
    out, made, failed = await deck_images.fill_images(filled)
    assert made == 1 and failed == 0
    assert '"data": "OLD"' in out
    assert len(models.calls) == 1
