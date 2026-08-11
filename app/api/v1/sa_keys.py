from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_session
from app.repositories import sa_keys as repo
from app.repositories import workers as workers_repo
from app.services import credential_id, credential_limiter, sa_key_vault, storage
from app.services.sa_key_validate import InvalidServiceAccountKey, parse_and_validate_sa_key

router = APIRouter(prefix="/sa-keys", tags=["sa-keys"])


async def _compensate_new_upload_if_definitively_uncommitted(
    *, row_id: UUID, sha256: str, created: bool
) -> None:
    """Remove bytes only after a fresh DB read proves our new row absent."""
    if not created:
        return
    try:
        async with SessionLocal() as fresh:
            row = await repo.get(fresh, row_id)
            if row is not None:
                return
    except Exception:
        return
    try:
        body = sa_key_vault.read_bytes(storage.sa_key_path(row_id))
        if hashlib.sha256(body).hexdigest() != sha256:
            return
        sa_key_vault.remove(storage.sa_key_path(row_id), missing_ok=True)
    except sa_key_vault.SAKeyVaultError:
        # Startup inventory reconciliation owns ambiguous filesystem residue.
        return


async def _reconcile_delete_outcome(
    *, row_id: UUID, sha256: str, ticket: sa_key_vault.DeleteQuarantine
) -> None:
    """Resolve an ambiguous DELETE/commit outcome from fresh DB authority."""
    try:
        async with SessionLocal() as fresh:
            row = await repo.get(fresh, row_id)
            observed_sha = row.sha256 if row is not None else None
    except Exception as exc:
        raise sa_key_vault.SAKeyVaultError(
            "SA-key delete outcome is unknown"
        ) from exc
    if observed_sha is None:
        sa_key_vault.discard_quarantined_delete(ticket)
        return
    if observed_sha == sha256:
        sa_key_vault.restore_quarantined_delete(ticket)
        return
    raise sa_key_vault.SAKeyVaultError("SA-key delete outcome is inconsistent")


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
    row, created = await repo.create_or_get_for_upload(
        session, original_filename=file.filename or "key.json",
        project_id=project_id, client_email=client_email, sha256=sha, byte_size=len(body),
    )
    row_id = row.id
    row_sha256 = row.sha256
    created_by_this_tx = bool(created)
    if hashlib.sha256(body).hexdigest() != row_sha256:
        await session.rollback()
        raise HTTPException(503, "SA-key upload metadata is inconsistent")
    path = storage.sa_key_path(row_id)
    must_write = created_by_this_tx
    if not must_write:
        try:
            current_sha256 = hashlib.sha256(
                sa_key_vault.read_bytes(path)
            ).hexdigest()
            must_write = current_sha256 != row_sha256
        except sa_key_vault.SAKeyVaultError:
            must_write = True
    if must_write:
        try:
            sa_key_vault.atomic_write(path, body)
        except sa_key_vault.SAKeyVaultError as exc:
            await session.rollback()
            raise HTTPException(503, "SA-key vault is unavailable") from exc
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await _compensate_new_upload_if_definitively_uncommitted(
            row_id=row_id,
            sha256=row_sha256,
            created=created_by_this_tx,
        )
        raise HTTPException(503, "SA-key upload did not commit") from None
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
    row, outcome = await repo.lock_unassigned_key_for_delete(session, key_id)
    if outcome == "not_found":
        raise HTTPException(404, "no such key")
    if outcome == "assigned":
        raise HTTPException(409, "key is still assigned to a worker; unassign first")
    if row is None:
        await session.rollback()
        raise HTTPException(503, "SA-key delete state is inconsistent")
    row_id = row.id
    row_sha256 = row.sha256
    try:
        ticket = sa_key_vault.quarantine_for_delete(
            storage.sa_key_path(row_id), expected_sha256=row_sha256
        )
    except sa_key_vault.SAKeyVaultError as exc:
        await session.rollback()
        raise HTTPException(503, "SA-key vault is unavailable") from exc
    try:
        deleted = await repo.delete_locked_key(session, row_id)
        if deleted != 1:
            raise RuntimeError("locked SA-key delete affected an unexpected row count")
        await session.commit()
    except Exception:
        await session.rollback()
        try:
            await _reconcile_delete_outcome(
                row_id=row_id, sha256=row_sha256, ticket=ticket
            )
        except sa_key_vault.SAKeyVaultError:
            pass
        raise HTTPException(503, "SA-key delete outcome is unavailable") from None
    try:
        sa_key_vault.discard_quarantined_delete(ticket)
    except sa_key_vault.SAKeyVaultError as exc:
        raise HTTPException(503, "SA-key vault is unavailable") from exc
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
    try:
        body = sa_key_vault.read_bytes(storage.sa_key_path(key_id))
    except sa_key_vault.SAKeyVaultError as exc:
        raise HTTPException(503, "SA-key vault is unavailable") from exc
    return Response(content=body, media_type="application/json")


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
    # Key row first, then exclusive host lock: one global lock order shared
    # with delete prevents an assignment/delete AB-BA cycle.
    # The following exclusive host lock serializes against a claim holding
    # the shared host lock, so a tombstone/re-key write cannot
    # interleave with a claim that already re-read "no tombstone". The
    # key_id existence check touches no host state, so it may run before
    # or after; kept before for a cheap 404 without taking the lock first.
    if await repo.lock_key_for_assignment(session, req.key_id) is None:
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
