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


@router.get("/grades/{grade_page_id}/available-languages")
async def get_available_languages(grade_page_id: str) -> dict:
    """Per-subject language availability for a grade page.

    Returns ``{app_subject: {lang: {"page_id": …, "has_textbook": …}}}`` — only
    subjects/languages where the container exists AND has a textbook PDF are
    included.  Consumers (e.g. the Fleet launcher) use this to decide which
    language badges to display and which source page to fetch the textbook from."""
    client = _client()
    try:
        return await asyncio.to_thread(
            notion_fetch.available_languages, client, grade_page_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Notion browse failed: {exc}")
