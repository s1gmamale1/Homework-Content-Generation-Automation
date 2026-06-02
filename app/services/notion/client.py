"""Sync Notion API wrapper: rate-limited notion_client.Client + raw httpx
2-step file upload. Ported (trimmed) from the s1gmamale1 reference.

This is synchronous on purpose; the async caller runs it via asyncio.to_thread.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

import httpx
from notion_client import Client

logger = logging.getLogger("notion.client")

_NOTION_VERSION = "2022-06-28"
_MIN_INTERVAL = 0.35  # ~3 req/s


class NotionClientWrapper:
    def __init__(self, api_key: str):
        key = (api_key or "").strip().strip('"').strip("'")
        if not key or not key.startswith(("ntn_", "secret_")):
            raise ValueError(
                "NOTION_API_KEY missing or invalid (must start with 'ntn_' or 'secret_')."
            )
        self.api_key = key
        self.client = Client(auth=self.api_key)
        self._min_interval = _MIN_INTERVAL
        self._last_request_time = 0.0
        self._request_count = 0

    def _rate_limit(self) -> None:
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()
        self._request_count += 1

    # ─── reads ───
    def get_block_children(self, block_id: str) -> list[dict]:
        results: list[dict] = []
        cursor = None
        while True:
            self._rate_limit()
            kwargs: dict = {"block_id": block_id}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self.client.blocks.children.list(**kwargs)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    def get_child_pages(self, parent_id: str) -> list[dict]:
        pages = []
        for block in self.get_block_children(parent_id):
            if block.get("type") == "child_page":
                pages.append(
                    {
                        "id": block["id"],
                        "title": block.get("child_page", {}).get("title", ""),
                        "type": "child_page",
                    }
                )
        return pages

    def page_has_content(self, page_id: str) -> bool:
        """True if the page already has any non-child_page block (idempotency guard)."""
        for block in self.get_block_children(page_id):
            if block.get("type") != "child_page":
                return True
        return False

    # ─── writes ───
    def create_page(self, parent_id: str, title: str, children: Optional[list[dict]] = None) -> dict:
        self._rate_limit()
        kwargs: dict = {
            "parent": {"page_id": parent_id},
            "properties": {"title": [{"text": {"content": title}}]},
        }
        if children:
            kwargs["children"] = children
        return self.client.pages.create(**kwargs)

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        results = []
        for i in range(0, len(children), 100):
            self._rate_limit()
            res = self.client.blocks.children.append(block_id=block_id, children=children[i : i + 100])
            results.extend(res.get("results", []))
        return {"results": results}

    # ─── file upload (2-step) ───
    def upload_bytes(self, data: bytes, file_name: str, content_type: str) -> str:
        auth = {"Authorization": f"Bearer {self.api_key}", "Notion-Version": _NOTION_VERSION}
        self._rate_limit()
        with httpx.Client(timeout=30.0) as http:
            r1 = http.post(
                "https://api.notion.com/v1/file_uploads",
                headers={**auth, "Content-Type": "application/json"},
                json={"filename": file_name, "content_type": content_type},
            )
        if r1.status_code not in (200, 201):
            raise RuntimeError(f"Notion file upload init failed: {r1.status_code} — {r1.text}")
        upload_id = r1.json()["id"]

        self._rate_limit()
        with httpx.Client(timeout=120.0) as http:
            r2 = http.post(
                f"https://api.notion.com/v1/file_uploads/{upload_id}/send",
                headers=auth,
                files={"file": (file_name, data, content_type)},
            )
        if r2.status_code not in (200, 201):
            raise RuntimeError(f"Notion file upload send failed: {r2.status_code} — {r2.text}")
        return upload_id
