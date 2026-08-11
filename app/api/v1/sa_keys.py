from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import sa_keys as repo
from app.repositories import workers as workers_repo
from app.services import credential_id, credential_limiter, storage
from app.services.sa_key_validate import InvalidServiceAccountKey, parse_and_validate_sa_key

router = APIRouter(prefix="/sa-keys", tags=["sa-keys"])


def _meta(row) -> dict:
    return {
        "id": str(row.id), "project_id": row.project_id,
        "client_email": row.client_email, "original_filename": row.original_filename,
        "label": row.label, "byte_size": row.byte_size, "worker_count": 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "max_concurrent_calls": row.max_concurrent_calls,
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
    # One grouped query over credential_slots for all rows (not per-row) —
    # STALE_TTL_SECONDS comes from credential_limiter so this never
    # re-derives the staleness window (task 6 brief).
    slots = await repo.slots_in_use_by_credential(
        session, credential_limiter.STALE_TTL_SECONDS
    )
    for k in keys:
        k["id"] = str(k["id"])
        k["created_at"] = k["created_at"].isoformat() if k["created_at"] else None
        # SA keys are gemini Vertex-SA credentials by construction — the
        # same string credential_for's own Vertex-pair branch builds (M6).
        credential = credential_id.gemini_project_credential(k["project_id"])
        k["slots_in_use"] = slots.get(credential, 0)
        k["effective_limit"] = await credential_limiter.resolve_limit(
            session, "gemini", credential
        )
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


class PatchMaxConcurrentRequest(BaseModel):
    # `ge=1` mirrors the DB CHECK (`ck_sa_keys_max_concurrent_calls_min`);
    # None (no override) is exempt from the numeric constraint in pydantic
    # v2 for an `int | None` field — only an explicit non-null value is
    # range-checked. FastAPI turns the resulting ValidationError into a 422.
    max_concurrent_calls: int | None = Field(default=None, ge=1)


@router.patch("/{key_id}")
async def patch_sa_key(
    key_id: UUID,
    req: PatchMaxConcurrentRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    # PROJECT-WIDE ATOMIC PATCH (codex #2): one UPDATE touches every sa_keys
    # row sharing key_id's project_id. rowcount==0 can only mean key_id
    # doesn't exist (a row always matches its own project_id), so that's
    # the 404 signal — no separate existence SELECT needed.
    rows_updated = await repo.set_max_concurrent_calls(
        session, key_id, req.max_concurrent_calls
    )
    if rows_updated == 0:
        raise HTTPException(404, "no such key")
    await session.commit()
    # Fresh `get()` — this session has not loaded `key_id` via the ORM yet
    # this request, so this issues a real SELECT rather than returning a
    # stale identity-mapped object left over from before the raw-SQL UPDATE.
    row = await repo.get(session, key_id)
    # Evict this project's cached resolve_limit entry (review fix, task 6):
    # without this, GET /sa-keys AND credential_limiter.acquire() would both
    # keep serving the pre-PATCH limit for up to _LIMIT_CACHE_TTL_SECONDS
    # (~60s) in THIS process. Scoped eviction — gemini_project_credential is
    # the one canonical builder for this string (credential_id.py), same as
    # list_sa_keys above. Other fleet workers hold their own process-local
    # cache and still lag up to ~60s until it naturally expires — a known,
    # accepted Task 4 trade-off, not fixed here.
    credential_limiter.evict_limit_cache(
        credential_id.gemini_project_credential(row.project_id)
    )
    return {**_meta(row), "rows_updated": rows_updated}


@router.get("/{key_id}/download")
async def download_sa_key(
    key_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await repo.get(session, key_id)
    if row is None:
        raise HTTPException(404, "no such key")
    path = storage.sa_key_path(key_id)
    if not path.exists():
        raise HTTPException(404, "key bytes missing on disk")
    return Response(content=path.read_bytes(), media_type="application/json")


# ---------------------------------------------------------------------------
# Assignment routes  (literal "/assignments" prefix). Starlette/FastAPI
# matches routes by method+path pattern independently, not by declaration
# order relative to OTHER methods — these GET/PUT/DELETE "/assignments..."
# literals work fine even though they're declared after the GET/PATCH/DELETE
# "/{key_id}" routes above, because none of those share a method+path with
# an "/assignments" route. (Route order only matters between two routes of
# the SAME method whose paths could both match the same request.)
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
    # Exclusive host lock BEFORE the mutation — serializes against a claim
    # holding the shared lock (task 1) so a tombstone/re-key write can't
    # interleave with a claim that already re-read "no tombstone". The
    # key_id existence check touches no host state, so it may run before
    # or after; kept before for a cheap 404 without taking the lock first.
    if await repo.get(session, req.key_id) is None:
        raise HTTPException(404, "no such key")
    await workers_repo.lock_host_exclusive(session, hostname)
    await repo.assign(session, hostname, req.key_id)
    await session.commit()
    return {"hostname": hostname, "key_id": str(req.key_id)}


@router.delete("/assignments/{hostname}")
async def unassign_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await workers_repo.lock_host_exclusive(session, hostname)
    await repo.unassign(session, hostname)
    await session.commit()
    return {"hostname": hostname, "unassigned": True}


@router.post("/assignments/{hostname}/scrub")
async def scrub_sa_key(hostname: str, session: AsyncSession = Depends(get_session)) -> dict:
    await workers_repo.lock_host_exclusive(session, hostname)
    await repo.scrub(session, hostname)
    await session.commit()
    return {"hostname": hostname, "scrub": True}
