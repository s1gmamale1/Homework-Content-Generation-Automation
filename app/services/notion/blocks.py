"""Pure Notion block builders + markdown→block conversion. No network I/O.

Ported from the s1gmamale1/Notion---Video-Lesson reference (tools/notion).
Notion limits respected: ≤2000 chars per rich_text segment.
"""

from __future__ import annotations

import re

_MAX_SEG = 2000


def _chunk(text: str, annotations: dict | None = None) -> list[dict]:
    segs: list[dict] = []
    for i in range(0, len(text), _MAX_SEG):
        seg: dict = {"type": "text", "text": {"content": text[i : i + _MAX_SEG]}}
        if annotations:
            seg["annotations"] = annotations
        segs.append(seg)
    return segs


def make_heading(text: str, level: int = 2) -> dict:
    htype = f"heading_{level}"
    return {
        "object": "block",
        "type": htype,
        htype: {"rich_text": [{"type": "text", "text": {"content": text[:_MAX_SEG]}}]},
    }


def make_paragraph(text: str, bold: bool = False) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _chunk(text, {"bold": True} if bold else None)},
    }


def make_divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def make_callout(text: str, emoji: str = "🖼️") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text[:_MAX_SEG]}}],
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def make_external_image(url: str) -> dict:
    return {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}}


_IMAGE_LINE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<url>[^)]*)\)$")


def make_file_upload_block(upload_id: str, name: str = "") -> dict:
    block: dict = {
        "object": "block",
        "type": "file",
        "file": {"type": "file_upload", "file_upload": {"id": upload_id}},
    }
    if name:
        block["file"]["name"] = name
    return block


def parse_rich_text(text: str) -> list[dict]:
    """Parse markdown **bold**/*italic*/***both*** into Notion rich_text.

    A lone '*' flanked by spaces/digits (multiplication) stays plain text.
    """
    segments: list[dict] = []
    pattern = (
        r"\*\*\*(.+?)\*\*\*"
        r"|\*\*(.+?)\*\*"
        r"|\*(?=[^\s*])(.+?)(?<=[^\s*])\*"
        r"|([^*]+|\*)"
    )
    for match in re.finditer(pattern, text):
        if match.group(1):
            content, annotations = match.group(1), {"bold": True, "italic": True}
        elif match.group(2):
            content, annotations = match.group(2), {"bold": True}
        elif match.group(3):
            content, annotations = match.group(3), {"italic": True}
        else:
            content, annotations = match.group(4), {}
        if not content:
            continue
        segments.extend(_chunk(content, annotations or None))
    if not segments:
        segments = [{"type": "text", "text": {"content": text[:_MAX_SEG]}}]
    return segments


def _rich_paragraph(text: str) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": parse_rich_text(text)}}


def markdown_to_notion_blocks(text: str) -> list[dict]:
    """Convert markdown to Notion blocks: #/##/### headings, --- dividers,
    -/* bullet lists, **bold**/*italic* inline, paragraphs."""
    out: list[dict] = []
    para: list[str] = []

    def _flush() -> None:
        if para:
            out.append(_rich_paragraph(" ".join(para)))
            para.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            _flush()
            continue
        if re.match(r"^-{3,}\s*$", stripped):
            _flush()
            out.append(make_divider())
            continue
        img = _IMAGE_LINE_RE.match(stripped)
        if img:
            _flush()
            url = img.group("url").strip()
            alt = img.group("alt").strip()
            if url.startswith(("http://", "https://")):
                out.append(make_external_image(url))
            else:
                # placeholder / non-resolving target → carry the description as a
                # callout (never an image block with an unresolvable URL).
                out.append(make_callout(alt or "visual placeholder"))
            continue
        h = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if h:
            _flush()
            out.append(make_heading(h.group(2), level=len(h.group(1))))
            continue
        b = re.match(r"^[-*]\s+(.+)$", stripped)
        if b:
            _flush()
            out.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": parse_rich_text(b.group(1))},
                }
            )
            continue
        para.append(stripped)
    _flush()
    return out
