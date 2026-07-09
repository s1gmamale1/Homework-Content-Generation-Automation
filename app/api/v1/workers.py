from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.repositories import budget as budget_repo
from app.repositories import sa_keys as sa_keys_repo
from app.repositories import workers as workers_repo

router = APIRouter()


@router.get("/workers")
async def list_workers(session: AsyncSession = Depends(get_session)) -> dict:
    """Head-side fleet liveness view: every worker + a derived `online` flag."""
    rows = await workers_repo.list_with_liveness(
        session, stale_after_seconds=settings.worker_registry_stale_seconds
    )
    online = sum(1 for r in rows if r["online"])
    assignments = await sa_keys_repo.list_assignments(session)
    for a in assignments:
        a["key_id"] = str(a["key_id"]) if a["key_id"] else None
    state = await budget_repo.get_state(session)
    return {
        "workers": rows,
        "total": len(rows),
        "online": online,
        "stale_after_seconds": settings.worker_registry_stale_seconds,
        "assignments": assignments,
        "version_floor": state.min_worker_version,
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


class VersionFloorIn(BaseModel):
    value: Optional[int] = Field(default=None, ge=0)


@router.put("/workers/version-floor")
async def put_version_floor(
    body: VersionFloorIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Operator escape hatch: set (may LOWER) or clear (value=null) the fleet
    version floor. The lifespan auto-stamp is raise-only; this is the way back
    down when a head accidentally stamps a floor above origin."""
    await budget_repo.set_version_floor(
        session, version=body.value, stamped_by="operator"
    )
    await session.commit()
    return {"version_floor": body.value}
