"""Per-slide illustration filling for the teacher pack.

The teacher-pack prompt has the model emit `ELEMENT: image` fences carrying
only `scene` + `caption` + `width` — no bytes. This module finds those
fences, generates each picture with Gemini's image model
(gemini-2.5-flash-image) using the SAME credential the text pipeline uses
(owner directive 2026-08-28; Imagen 404s on this endpoint) and the owner's
fixed style prefix, recompresses to JPEG, and injects the base64 as `data`.

Contract with the platform importer (DIRECTIVE-slides-lean-plus-images):
- an image element with missing/invalid base64 HARD-FAILS the packet at
  import, so a failed generation must remove the WHOLE fence, never ship a
  data-less one;
- readable text/formulas never go into the image — the style prefix forbids
  them and the prompt keeps them out of `scene`.

Fail-open everywhere: no credential, endpoint down, or every image failing
must NEVER fail the job — the deck ships imageless with a warning and the
importer-side audit flags it.
"""

from __future__ import annotations

import asyncio
import json
import re

from loguru import logger

from app.config import settings

# The owner's fixed style prompt (verbatim from the directive). The scene is
# interpolated at the head; the style handles palette/composition and forbids
# readable text, logos and watermarks.
STYLE_PREFIX = (
    "Create an illustration of {scene} in my flat-vector illustration style: "
    "a friendly modern flat-vector illustration with a soft educational "
    "children's-book aesthetic. Use simplified rounded characters and objects, "
    "clean smooth shapes, minimal outlines, expressive but simple facial "
    "features, and gently exaggerated proportions. Use a light pastel palette "
    "of pale blue, turquoise, teal, mint green, warm yellow, cream, and subtle "
    "peach tones. Add soft diffused lighting, mild gradients, restrained "
    "shadows, and a crisp polished 2D finish. Use an eye-level landscape "
    "composition with the main subject clearly centered, important objects "
    "visible in the foreground, and a bright, tidy professional environment. "
    "Add lightly faded contextual symbols and subtle plants in the background. "
    "Keep the atmosphere optimistic, welcoming, focused, and professional. "
    "Polished commercial educational illustration quality. No photorealism, "
    "dark colors, heavy outlines, complex textures, dramatic contrast, "
    "readable text, logos, or watermarks."
)

# Review 2026-08-28 (VERDICT-v186): two leaks fixed here. "logistics" in the
# original owner prefix named the AESTHETIC but the model drew delivery
# imagery — the prefix now says "flat-vector illustration style" (rest kept
# verbatim). And scenes implying calculations produced garbled chalkboard
# text — every prompt now carries the hard no-text clause, scenes are
# sanitized of digits/equation symbols, and a scene that mentions writable
# surfaces or numeric ideas gets the even harder BLANK-surfaces variant.
_HARD_NO_TEXT = (
    " — absolutely NO letters, numbers, digits, equations, or writing of ANY "
    "kind anywhere in the image; no chalkboard text, no labels, no signage. "
    "The subject of the picture is the described scene itself — never "
    "delivery, warehouses, packages, trucks, or logistics imagery."
)
_HARD_NO_TEXT_BLANK = (
    _HARD_NO_TEXT
    + " Any board, screen, sign, paper or surface in the scene is completely "
    "BLANK, with nothing written or drawn on it."
)
_RISKY_SCENE_RE = re.compile(
    r"(?i)\b(chalk\s*board|blackboard|whiteboard|board|screen|sign|label|"
    r"poster|paper|notebook|writing|written|text|formula|equation|number|"
    r"digit|calculat\w*)\b"
)
_SCENE_STRIP_RE = re.compile(r"[0-9=+×÷<>%^_/\\$]|\b[a-z]\)\s")


def _build_prompt(scene: str) -> str:
    clean = _SCENE_STRIP_RE.sub(" ", scene)
    clean = re.sub(r"\s+", " ", clean).strip()
    tail = _HARD_NO_TEXT_BLANK if _RISKY_SCENE_RE.search(scene) else _HARD_NO_TEXT
    return STYLE_PREFIX.format(scene=clean) + tail

# Both tolerated fence forms: kind on the fence line, or on the first line
# inside (canonical). Group 1/2 = kind, group 3 = JSON body.
_IMG_FENCE_RE = re.compile(
    r"```(?:ELEMENT:[ \t]*(image)[ \t]*\n|[ \t]*\nELEMENT:[ \t]*(image)[ \t]*\n)"
    r"(.*?)```",
    re.S,
)


def _client():
    """The Gemini client — same auth as the text pipeline (owner directive:
    the SAME key that generates the text generates the images). Reuses
    api_transport._gemini_client so key vs Vertex-SA handling stays
    single-sourced."""
    from app.services.api_transport import _gemini_client

    return _gemini_client(settings.deck_image_model)


def _max_side() -> int:
    """Longest-side target parsed from deck_image_size ('1536x1024' → 1536)."""
    try:
        return max(int(p) for p in settings.deck_image_size.lower().split("x"))
    except Exception:  # noqa: BLE001
        return 1536


def _recompress(raw: bytes) -> bytes:
    """PNG-ish model output → JPEG (quality 60), downscaled to the target
    longest side. gemini-2.5-flash-image returns ~1MB PNGs and accepts no
    size/format parameters, so the packet budget is enforced here. Fail-open:
    any Pillow trouble returns the original bytes."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        side = _max_side()
        if max(img.size) > side:
            img.thumbnail((side, side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60, optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"deck_images: recompress failed ({exc!r}); keeping original bytes")
        return raw


async def _generate_one(client, scene: str) -> str:
    """One image call → base64 string (JPEG after recompression).

    gemini-2.5-flash-image ("nano-banana") is a generate_content model whose
    response carries the picture as an inline_data part — smoke-verified
    2026-08-28 (Imagen models 404 on this endpoint)."""
    import base64

    prompt = _build_prompt(scene)
    resp = await client.aio.models.generate_content(
        model=settings.deck_image_model, contents=prompt,
    )
    for cand in resp.candidates or ():
        for part in (cand.content.parts or ()) if cand.content else ():
            blob = getattr(part, "inline_data", None)
            if blob is not None and blob.data:
                return base64.b64encode(_recompress(blob.data)).decode("ascii")
    raise RuntimeError("image response carried no inline_data part")


async def fill_images(md: str, *, job_id=None) -> tuple[str, int, int]:
    """Inject generated bytes into every data-less image element.

    Returns (new_md, generated, failed). Fences whose generation fails (or
    that exceed the per-deck cap) are REMOVED entirely. Raises only
    AuthEnvError-shaped credential absence to the caller's discretion — no:
    even that is caught here; a missing key returns the md untouched with
    every image counted failed=0/generated=0 and a note via logger (the
    caller adds the warning).
    """
    matches = list(_IMG_FENCE_RE.finditer(md))
    todo = []
    for m in matches:
        try:
            obj = json.loads(m.group(3))
        except Exception:  # noqa: BLE001 — malformed json → strip at apply time
            todo.append((m, None))
            continue
        if obj.get("data"):
            continue  # already filled
        todo.append((m, obj))
    if not todo:
        return md, 0, 0

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 — no credential on this machine
        logger.warning(f"deck_images: no image client ({exc!r}) — deck ships imageless")
        # strip ALL data-less fences: the importer hard-fails on data-less ones
        out = md
        for m, _ in reversed(todo):
            out = out[:m.start()] + out[m.end():]
        return out, 0, len(todo)

    sem = asyncio.Semaphore(3)
    results: dict[int, str | None] = {}

    async def _run(idx: int, obj) -> None:
        if obj is None or not obj.get("scene"):
            results[idx] = None
            return
        async with sem:
            try:
                results[idx] = await _generate_one(client, str(obj["scene"])[:500])
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"deck_images[job {job_id}]: scene {idx} failed ({exc!r})"
                )
                results[idx] = None

    capped = todo[: settings.deck_image_max]
    for m, _ in todo[settings.deck_image_max:]:
        logger.info(f"deck_images[job {job_id}]: over cap — stripping extra image")
    await asyncio.gather(*(_run(i, obj) for i, (_, obj) in enumerate(capped)))

    made = failed = 0
    out = md
    # apply right-to-left so match offsets stay valid
    for rev_idx in range(len(todo) - 1, -1, -1):
        m, obj = todo[rev_idx]
        b64 = results.get(rev_idx) if rev_idx < len(capped) else None
        if b64 and obj is not None:
            obj["data"] = b64
            block = "```\nELEMENT: image\n" + json.dumps(obj, ensure_ascii=False) + "\n```"
            out = out[:m.start()] + block + out[m.end():]
            made += 1
        else:
            out = out[:m.start()] + out[m.end():]
            failed += 1
    return out, made, failed
