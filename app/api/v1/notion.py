import asyncio

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services import notion_fetch
from app.services.notion.client import NotionClientWrapper

router = APIRouter(prefix="/notion", tags=["notion"])


def _client() -> NotionClientWrapper:
    if not settings.notion_api_key:
        raise HTTPException(503, "Notion not configured")
    return NotionClientWrapper(api_key=settings.notion_api_key)


@router.get("/grades")
async def get_grades() -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(
            notion_fetch.list_grades, client, settings.notion_lessons_root)
    except Exception as exc:  # noqa: BLE001 - surface a clean "unavailable" to the wizard
        raise HTTPException(502, f"Notion browse failed: {exc}")


@router.get("/grades/{grade_page_id}/subjects")
async def get_subjects(grade_page_id: str) -> list[dict]:
    client = _client()
    try:
        return await asyncio.to_thread(
            notion_fetch.list_subjects, client, grade_page_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Notion browse failed: {exc}")
