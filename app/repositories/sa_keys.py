from __future__ import annotations

from typing import Literal
from uuid import UUID

from sqlalchemy import delete as sa_delete, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import _utcnow
from app.models.sa_key import SAKey, SAKeyAssignment


async def create_or_get(
    session: AsyncSession,
    *,
    original_filename: str,
    project_id: str,
    client_email: str,
    sha256: str,
    byte_size: int,
    label: str | None = None,
) -> SAKey:
    """Insert a new SA key row, or return the existing row if sha256 already
    present (dedup gate so re-uploads of the same JSON file are no-ops)."""
    existing = await session.scalar(select(SAKey).where(SAKey.sha256 == sha256))
    if existing is not None:
        return existing
    row = SAKey(
        original_filename=original_filename,
        project_id=project_id,
        client_email=client_email,
        sha256=sha256,
        byte_size=byte_size,
        label=label,
    )
    session.add(row)
    await session.flush()
    return row


async def create_or_get_for_upload(
    session: AsyncSession, **values
) -> tuple[SAKey, bool]:
    """Race-safe upload ownership with the dedup row locked for file repair."""
    inserted_id = await session.scalar(
        pg_insert(SAKey)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["sha256"])
        .returning(SAKey.id)
    )
    if inserted_id is not None:
        row = await session.get(SAKey, inserted_id)
        if row is None:  # defensive: RETURNING identified a row in this transaction
            raise RuntimeError("inserted SA-key upload row was not readable")
        return row, True
    row = await session.scalar(
        select(SAKey)
        .where(SAKey.sha256 == values["sha256"])
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("SA-key upload conflict did not resolve")
    return row, False


async def get(session: AsyncSession, key_id: UUID) -> SAKey | None:
    return await session.get(SAKey, key_id)


async def lock_key_for_assignment(
    session: AsyncSession, key_id: UUID
) -> SAKey | None:
    """Take PostgreSQL FOR KEY SHARE before binding a worker to this key."""
    return await session.scalar(
        select(SAKey)
        .where(SAKey.id == key_id)
        .with_for_update(read=True, key_share=True)
    )


async def lock_unassigned_key_for_delete(
    session: AsyncSession, key_id: UUID
) -> tuple[SAKey | None, Literal["ready", "not_found", "assigned"]]:
    """Lock the key row exclusively, then decide delete eligibility."""
    row = await session.scalar(
        select(SAKey).where(SAKey.id == key_id).with_for_update()
    )
    if row is None:
        return None, "not_found"
    assigned = await session.scalar(
        select(
            exists().where(SAKeyAssignment.key_id == key_id)
        )
    )
    if assigned:
        return row, "assigned"
    return row, "ready"


async def delete_locked_key(session: AsyncSession, key_id: UUID) -> int:
    result = await session.execute(sa_delete(SAKey).where(SAKey.id == key_id))
    return result.rowcount or 0


async def uuid_hash_inventory(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(SAKey.id, SAKey.sha256))).all()
    return {f"{key_id}.json": sha256 for key_id, sha256 in rows}


async def list_keys(session: AsyncSession) -> list[dict]:
    """Return all SA key metadata rows with a `worker_count` field.
    Never includes private_key material (which lives on disk, not in the DB)."""
    counts = dict(
        (
            await session.execute(
                select(SAKeyAssignment.key_id, func.count())
                .where(SAKeyAssignment.key_id.is_not(None))
                .group_by(SAKeyAssignment.key_id)
            )
        ).all()
    )
    rows = (
        await session.execute(select(SAKey).order_by(SAKey.created_at))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "client_email": r.client_email,
            "original_filename": r.original_filename,
            "label": r.label,
            "byte_size": r.byte_size,
            "created_at": r.created_at,
            "worker_count": int(counts.get(r.id, 0)),
            "max_concurrent_calls": r.max_concurrent_calls,
        }
        for r in rows
    ]


async def set_max_concurrent_calls(
    session: AsyncSession, key_id: UUID, value: int | None
) -> int:
    """Project-wide atomic override write (codex #2, BE-16 task 6):
    ``project_id`` has no unique constraint on ``sa_keys`` — two rows can
    legitimately share one GCP project — so this updates EVERY row sharing
    the target row's project_id in ONE UPDATE statement, never just the
    named row alone (which could leave two keys for the same project
    disagreeing on their effective limit).

    Returns the number of rows updated. ``0`` means ``key_id`` doesn't
    exist (the subquery WHERE clause matches nothing) — the API layer
    turns that into a 404. The DB-level CHECK constraint
    (``ck_sa_keys_max_concurrent_calls_min``) is the last line of defense;
    the API's pydantic ``Field(ge=1)`` should already have rejected
    sub-1 non-null values before this is ever called.
    """
    result = await session.execute(
        text(
            "UPDATE sa_keys SET max_concurrent_calls = :value "
            "WHERE project_id = (SELECT project_id FROM sa_keys WHERE id = :key_id)"
        ),
        {"value": value, "key_id": key_id},
    )
    return result.rowcount or 0


async def slots_in_use_by_credential(
    session: AsyncSession, ttl_seconds: int
) -> dict[str, int]:
    """One grouped count over ``credential_slots`` for in-flight visibility
    (BE-16 task 6). Only rows fresher than ``ttl_seconds`` count as
    "in use" — the caller passes ``credential_limiter.STALE_TTL_SECONDS``
    so this never re-derives the staleness window."""
    rows = (
        await session.execute(
            text(
                "SELECT credential, count(*) AS n FROM credential_slots "
                "WHERE acquired_at > now() - make_interval(secs => :ttl) "
                "GROUP BY credential"
            ),
            {"ttl": ttl_seconds},
        )
    ).all()
    return {r.credential: int(r.n) for r in rows}


async def delete(session: AsyncSession, key_id: UUID) -> str:
    """Delete an SA key.

    Returns:
        "deleted"   — row removed.
        "not_found" — no such key.
        "assigned"  — one or more workers reference this key; delete blocked.
    """
    row = await session.get(SAKey, key_id)
    if row is None:
        return "not_found"
    assigned = await session.scalar(
        select(func.count())
        .select_from(SAKeyAssignment)
        .where(SAKeyAssignment.key_id == key_id)
    )
    if assigned and assigned > 0:
        return "assigned"
    await session.execute(sa_delete(SAKey).where(SAKey.id == key_id))
    return "deleted"


async def assign(session: AsyncSession, hostname: str, key_id: UUID) -> None:
    stmt = pg_insert(SAKeyAssignment).values(
        hostname=hostname, key_id=key_id, scrub_requested_at=None, updated_at=_utcnow(),
    ).on_conflict_do_update(
        index_elements=["hostname"],
        set_={"key_id": key_id, "scrub_requested_at": None, "updated_at": _utcnow()},
    )
    await session.execute(stmt)


async def unassign(session: AsyncSession, hostname: str) -> bool:
    res = await session.execute(
        sa_delete(SAKeyAssignment).where(SAKeyAssignment.hostname == hostname)
    )
    return (res.rowcount or 0) > 0


async def scrub(session: AsyncSession, hostname: str) -> None:
    stmt = pg_insert(SAKeyAssignment).values(
        hostname=hostname, key_id=None, scrub_requested_at=_utcnow(), updated_at=_utcnow(),
    ).on_conflict_do_update(
        index_elements=["hostname"],
        set_={"key_id": None, "scrub_requested_at": _utcnow(), "updated_at": _utcnow()},
    )
    await session.execute(stmt)


async def scrub_pending_for_host(session: AsyncSession, hostname: str) -> bool:
    """True iff `hostname` has an assignment row with a scrub in flight
    (`scrub_requested_at IS NOT NULL` — the same signal `scrub()` sets and
    `assign()` clears). False both when the row is keyed to a live SA key
    and when there is no assignment row at all for this host."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    SAKeyAssignment.hostname == hostname,
                    SAKeyAssignment.scrub_requested_at.is_not(None),
                )
            )
        )
    )


async def get_assignment_with_key(session: AsyncSession, hostname: str) -> dict | None:
    row = (await session.execute(
        select(SAKeyAssignment, SAKey)
        .outerjoin(SAKey, SAKeyAssignment.key_id == SAKey.id)
        .where(SAKeyAssignment.hostname == hostname)
    )).first()
    if row is None:
        return None
    asg, key = row
    return {
        "key_id": asg.key_id,
        "sha256": key.sha256 if key is not None else None,
        "project_id": key.project_id if key is not None else None,
        "scrub": asg.scrub_requested_at is not None,
    }


async def list_assignments(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        select(SAKeyAssignment, SAKey)
        .outerjoin(SAKey, SAKeyAssignment.key_id == SAKey.id)
        .order_by(SAKeyAssignment.hostname)
    )).all()
    return [
        {
            "hostname": asg.hostname,
            "key_id": asg.key_id,
            "project_id": key.project_id if key is not None else None,
            "label": key.label if key is not None else None,
            "scrub": asg.scrub_requested_at is not None,
        }
        for asg, key in rows
    ]
