from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import workers as workers_repo

router = APIRouter()


@router.get("/workers")
async def list_workers(session: AsyncSession = Depends(get_session)) -> dict:
    """Head-side fleet liveness view: every worker + a derived `online` flag."""
    rows = await workers_repo.list_with_liveness(
        session, stale_after_seconds=settings.worker_registry_stale_seconds
    )
    online = sum(1 for r in rows if r["online"])
    return {
        "workers": rows,
        "total": len(rows),
        "online": online,
        "stale_after_seconds": settings.worker_registry_stale_seconds,
    }


@router.post("/workers/{pc_id}/drain")
async def drain_worker(pc_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Set worker status to 'draining' so it finishes current jobs and stops claiming new ones."""
    ok = await workers_repo.set_status(session, pc_id, "draining")
    if not ok:
        raise HTTPException(status_code=404, detail="worker not found")
    await session.commit()
    return {"pc_id": pc_id, "status": "draining"}


@router.post("/workers/{pc_id}/undrain")
async def undrain_worker(pc_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Cancel a drain, returning worker to 'online' before the next heartbeat picks it up."""
    ok = await workers_repo.set_status(session, pc_id, "online")
    if not ok:
        raise HTTPException(status_code=404, detail="worker not found")
    await session.commit()
    return {"pc_id": pc_id, "status": "online"}
