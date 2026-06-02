"""Idempotent find-or-create of a child page by normalized title.

Ported (simplified) from the reference page_creator: we only ever create a
lesson page and a single `Homework` sub-page, never the full 12-sub-page template.
"""

from __future__ import annotations

import re

from .client import NotionClientWrapper


def _normalize(title: str) -> str:
    # strip trailing "(N)" dedup suffixes Notion appends, lowercase, trim
    return re.sub(r"\s*\(\d+\)\s*$", "", title.strip()).strip().lower()


def find_or_create(client: NotionClientWrapper, parent_id: str, title: str) -> tuple[str, bool]:
    """Return (page_id, created). Reuses an existing child whose normalized
    title matches; otherwise creates a new child page."""
    existing = {_normalize(c["title"]): c["id"] for c in client.get_child_pages(parent_id)}
    norm = _normalize(title)
    if norm in existing:
        return existing[norm], False
    page = client.create_page(parent_id, title.strip())
    return page["id"], True
