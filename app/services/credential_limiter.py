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
from sqlalchemy.ext.asyncio import AsyncSession

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

# Poll backoff: exponential decay 1s -> 5s (doubling each miss, capped),
# plus jitter so a thundering herd of waiters doesn't all re-check in
# lockstep (codex-review #6 — a saturated credential can have dozens of
# waiters; a fixed 1s poll is needlessly chatty once a waiter has already
# missed a few times).
_POLL_INTERVAL_MIN_S = 1.0
_POLL_INTERVAL_MAX_S = 5.0
_POLL_BACKOFF_FACTOR = 2.0
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

    Polls once per short-lived session/transaction; sleeps between polls
    with exponential backoff (``1s`` -> ``5s``, doubling each miss, + jitter,
    clipped to the remaining budget), never holding a connection across the
    sleep.
    """
    if not limit or limit <= 0:
        return BYPASS

    deadline = time.monotonic() + wait_budget_s
    poll_interval = _POLL_INTERVAL_MIN_S
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
        sleep_for = min(poll_interval + random.uniform(0, _POLL_JITTER_S), remaining)
        await asyncio.sleep(max(0.0, sleep_for))
        poll_interval = min(poll_interval * _POLL_BACKOFF_FACTOR, _POLL_INTERVAL_MAX_S)


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


# ─── Limit resolution (BE-16 task 4) ───────────────────────────────────────
# Per-credential cache: credential -> (limit, expires_at_monotonic). Module-
# level and exposed directly (not hidden behind a class) so tests can both
# inspect and reset it deterministically via ``clear_limit_cache``.
_LIMIT_CACHE: dict[str, tuple[int, float]] = {}
_LIMIT_CACHE_TTL_SECONDS: float = 60.0

# gemini is the only provider whose credential can carry a per-key override:
# `sa_keys.max_concurrent_calls` is a GCP-service-account-key concept, and
# only Vertex-SA-form gemini credentials (`gemini:{project}`, see
# credential_id.credential_for) are project-shaped. claude/clodex — and
# gemini's own API-key fingerprint form (`gemini:{sha256[:16]}`, which never
# matches a real `project_id`) — always resolve straight to the provider env
# default with no DB round-trip.
_GEMINI_PREFIX = "gemini:"


def clear_limit_cache() -> None:
    """Test helper: wipe the module-level ``resolve_limit`` cache."""
    _LIMIT_CACHE.clear()


def evict_limit_cache(credential: str) -> None:
    """Drop one credential's cached ``resolve_limit`` entry.

    Called by the sa-keys PATCH route (task 6 review fix) right after it
    commits a ``max_concurrent_calls`` override, so the next `resolve_limit`
    call for this credential re-reads the DB instead of serving the stale
    value for up to ``_LIMIT_CACHE_TTL_SECONDS``. Scoped (not a wholesale
    ``clear_limit_cache``) so an override on one project doesn't cost every
    other in-flight credential's cache. A no-op if the credential was never
    cached (`dict.pop` with a default).

    This is per-process: other fleet workers hold their own cache and keep
    serving the old limit for up to ~60s until it naturally expires (Task 4
    trade-off — see module docstring's `_LIMIT_CACHE_TTL_SECONDS`).
    """
    _LIMIT_CACHE.pop(credential, None)


async def resolve_limit(session: AsyncSession, provider: str, credential: str) -> int:
    """Resolve the effective per-credential api concurrency cap.

    A `gemini:{project}` credential resolves
    ``SELECT MIN(max_concurrent_calls) FROM sa_keys WHERE project_id = :p AND
    max_concurrent_calls IS NOT NULL`` — `sa_keys.project_id` has no unique
    constraint (only `sha256` does), so two SA-key rows can legitimately name
    the same GCP project; MIN is the deterministic, conservative pick
    (codex-review #2). No matching non-null override (including no matching
    row at all, which is what always happens for a gemini API-key
    fingerprint credential) falls back to
    ``settings.credential_max_concurrent_<provider>``.

    Cached per credential for ``_LIMIT_CACHE_TTL_SECONDS`` (~60s). A DB error
    is **never cached** (review M3) — it returns the provider default for
    this call only, so the next call retries against the DB instead of
    sticking to a stale fail-open value for the rest of the TTL window.
    """
    now = time.monotonic()
    cached = _LIMIT_CACHE.get(credential)
    if cached is not None and cached[1] > now:
        return cached[0]

    default = getattr(settings, f"credential_max_concurrent_{provider}", 0)

    project = (
        credential[len(_GEMINI_PREFIX):]
        if provider == "gemini" and credential.startswith(_GEMINI_PREFIX)
        else None
    )
    if project is None:
        _LIMIT_CACHE[credential] = (default, now + _LIMIT_CACHE_TTL_SECONDS)
        return default

    try:
        result = await session.execute(
            text(
                "SELECT MIN(max_concurrent_calls) FROM sa_keys "
                "WHERE project_id = :project AND max_concurrent_calls IS NOT NULL"
            ),
            {"project": project},
        )
        override = result.scalar_one_or_none()
    except Exception:
        logger.exception("credential_limiter.resolve_limit failed for %s", credential)
        return default  # NOT cached — see docstring

    limit = default if override is None else override
    _LIMIT_CACHE[credential] = (limit, now + _LIMIT_CACHE_TTL_SECONDS)
    return limit
