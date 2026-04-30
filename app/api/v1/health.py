from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    db_ok = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = f"error: {e}"
    return {"status": "ok", "db": db_ok}
