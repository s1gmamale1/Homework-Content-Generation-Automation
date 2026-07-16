"""Postgres-backed fleet-wide per-credential api concurrency limiter (BE-16
task 3). Core primitives ONLY — no wiring into `agent.py` (task 5) and no
limit resolution from `sa_keys.max_concurrent_calls` (task 4).

Design (see docs/superpowers/plans/2026-07-16-credential-rate-limit.md):
`credential_slots(id, credential, pc_id, acquired_at)` has NO SQLAlchemy
model (migration 0047 comment) — every access here is raw SQL via
``sqlalchemy.text()``. A row is a held "slot" against a credential
fingerprint (see ``credential_id.credential_for``); the count of *fresh*
rows (younger than ``STALE_TTL_SECONDS``) for a credential is compared
against the caller-supplied ``limit``.

Concurrency is serialized per credential via
``pg_advisory_xact_lock(hashtext(credential))`` — a transaction-scoped
advisory lock that auto-releases at commit/rollback, so a crashed holder
can never wedge the lock. The count-then-insert happens INSIDE that same
transaction so two concurrent callers can't both observe `count < limit`
and both insert (TOCTOU).

Each poll iteration in ``acquire`` opens and closes its OWN short session —
never held across the inter-poll sleep, so a slow poller doesn't pin a pool
connection for the whole ``wait_budget_s`` window.

STALE_TTL: reviewed (I5) at exactly 2x the per-attempt timeout — a TTL equal
to the timeout itself would expire (and thus stop counting) the slot of a
legitimately still-running call sitting right at its own timeout, causing
this limiter to under-count real concurrency and over-admit callers. This
value is DERIVED from ``settings.per_attempt_timeout_seconds``, not
hardcoded, so it tracks that setting if it ever changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import time
from typing import Optional, Union
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)

STALE_TTL_SECONDS: int = 2 * settings.per_attempt_timeout_seconds

# Sentinel returned by `acquire` (and accepted as a no-op by `release`) when
# the caller passes a non-positive/None limit — i.e. "no fleet-wide cap for
# this credential". Distinct from `None`, which `acquire` uses to mean
# "wait_budget_s exhausted, no slot" — callers must be able to tell the two
# apart (task 5 wiring skips the limiter entirely on BYPASS; a real `None`
# means "back off, budget was exceeded, don't run the call").
BYPASS = object()

# `pc_id` default: identifies which process/host is holding a slot, for
# operator debugging (review M7). Computed once at import time.
PC_ID: str = f"{socket.gethostname()}:{os.getpid()}"

# Base poll interval; jitter avoids a thundering herd of waiters all
# re-checking in lockstep.
_POLL_INTERVAL_S = 1.0
_POLL_JITTER_S = 0.5

SlotId = Union[UUID, object]


async def acquire(
    credential: str,
    limit: Optional[int],
    *,
    pc_id: str = PC_ID,
    wait_budget_s: float,
) -> Optional[SlotId]:
    """Try to hold a concurrency slot for ``credential``.

    Returns:
    - ``BYPASS`` immediately, without touching the DB, if ``limit`` is
      ``None`` or <= 0 (no cap configured for this credential).
    - a slot id (pass to ``release``) once a slot is available.
    - ``None`` if ``wait_budget_s`` is exhausted without ever getting one.

    Polls once per short-lived session/transaction; sleeps ``1s`` (+ jitter)
    between polls, never holding a connection across the sleep.
    """
    if not limit or limit <= 0:
        return BYPASS

    deadline = time.monotonic() + wait_budget_s
    while True:
        async with SessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:cred))"),
                    {"cred": credential},
                )
                count = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM credential_slots "
                            "WHERE credential = :cred "
                            "AND acquired_at > now() - make_interval(secs => :ttl)"
                        ),
                        {"cred": credential, "ttl": STALE_TTL_SECONDS},
                    )
                ).scalar_one()
                if count < limit:
                    slot_id = (
                        await session.execute(
                            text(
                                "INSERT INTO credential_slots (credential, pc_id) "
                                "VALUES (:cred, :pc_id) RETURNING id"
                            ),
                            {"cred": credential, "pc_id": pc_id},
                        )
                    ).scalar_one()
                    return slot_id

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        sleep_for = min(_POLL_INTERVAL_S + random.uniform(0, _POLL_JITTER_S), remaining)
        await asyncio.sleep(max(0.0, sleep_for))


async def release(slot_id: Optional[SlotId]) -> None:
    """Release a slot previously returned by ``acquire``.

    A no-op for ``BYPASS``/``None`` (never touches the DB) and for a slot id
    that no longer exists (already swept, or double-release) — DELETE
    matching zero rows is not an error.
    """
    if slot_id is BYPASS or slot_id is None:
        return
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("DELETE FROM credential_slots WHERE id = :id"),
                {"id": slot_id},
            )


async def sweep() -> int:
    """Delete slot rows older than ``STALE_TTL_SECONDS`` (crashed holders
    that never released). Own session, own try/except (review M1) — this
    must NEVER run inside worker.py's `_sweep_stuck_jobs` single
    `session.begin()`; a limiter-table error must not abort job reclaims.

    Returns the number of rows deleted, or 0 on any DB error (logged, not
    raised).
    """
    try:
        async with SessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "DELETE FROM credential_slots "
                        "WHERE acquired_at <= now() - make_interval(secs => :ttl)"
                    ),
                    {"ttl": STALE_TTL_SECONDS},
                )
                return result.rowcount or 0
    except Exception:
        logger.exception("credential_limiter.sweep failed")
        return 0
