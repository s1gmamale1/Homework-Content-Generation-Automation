from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_strict
from app.db import get_session
from app.repositories import sa_keys as repo
from app.services import storage
from app.services.sa_key_validate import InvalidServiceAccountKey, parse_and_validate_sa_key

router = APIRouter(prefix="/sa-keys", tags=["sa-keys"])


def _meta(row) -> dict:
    return {
        "id": str(row.id), "project_id": row.project_id,
        "client_email": row.client_email, "original_filename": row.original_filename,
        "label": row.label, "byte_size": row.byte_size,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("", status_code=201)
async def upload_sa_key(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    body = await file.read()
    try:
        project_id, client_email = parse_and_validate_sa_key(body)
    except InvalidServiceAccountKey as exc:
        raise HTTPException(422, f"not a valid service-account key: {exc}")
    sha = hashlib.sha256(body).hexdigest()
    row = await repo.create_or_get(
        session, original_filename=file.filename or "key.json",
        project_id=project_id, client_email=client_email, sha256=sha, byte_size=len(body),
    )
    await session.commit()
    path = storage.sa_key_path(row.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)  # idempotent on dedup (same bytes)
    return _meta(row)


@router.get("")
async def list_sa_keys(session: AsyncSession = Depends(get_session)) -> dict:
    keys = await repo.list_keys(session)
    for k in keys:
        k["id"] = str(k["id"])
        k["created_at"] = k["created_at"].isoformat() if k["created_at"] else None
    return {"keys": keys}


@router.delete("/{key_id}")
async def delete_sa_key(key_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    outcome = await repo.delete(session, key_id)
    if outcome == "not_found":
        raise HTTPException(404, "no such key")
    if outcome == "assigned":
        raise HTTPException(409, "key is still assigned to a worker; unassign first")
    await session.commit()
    storage.sa_key_path(key_id).unlink(missing_ok=True)
    return {"deleted": str(key_id)}


@router.get("/{key_id}/download")
async def download_sa_key(
    key_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: dict = Depends(get_current_user_strict),  # header-only; overrides router dep
) -> Response:
    row = await repo.get(session, key_id)
    if row is None:
        raise HTTPException(404, "no such key")
    path = storage.sa_key_path(key_id)
    if not path.exists():
        raise HTTPException(404, "key bytes missing on disk")
    return Response(content=path.read_bytes(), media_type="application/json")


# ---------------------------------------------------------------------------
# Assignment routes  (literal "/assignments" prefix — declared before any
# {key_id} wildcard so FastAPI resolves "assignments" as a literal, not a UUID)
# ---------------------------------------------------------------------------

class AssignRequest(BaseModel):
    key_id: UUID


@router.get("/assignments")
async def list_assignments(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await repo.list_assignments(session)
    for r in rows:
        r["key_id"] = str(r["key_id"]) if r["key_id"] else None
    return {"assignments": rows}


@router.put("/assignments/{hostname}")
async def assign_sa_key(
    hostname: str, req: AssignRequest, session: AsyncSession = Depends(get_session),
) -> dict:
    if await repo.get(session, req.key_id) is None:
        raise HTTPException(404, "no such key")
    await repo.assign(session, hostname, req.key_id)
    await session.commit()
    return {"hostname": hostname, "key_id": str(req.key_id)}


@router.delete("/assignments/{hostname}")
async def unassign_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await repo.unassign(session, hostname)
    await session.commit()
    return {"hostname": hostname, "unassigned": True}


@router.post("/assignments/{hostname}/scrub")
async def scrub_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await repo.scrub(session, hostname)
    await session.commit()
    return {"hostname": hostname, "scrub": True}
