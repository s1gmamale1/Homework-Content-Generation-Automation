"""Postgres-backed fleet-wide per-credential api concurrency limiter (BE-16
task 3). Core primitives ONLY — no wiring into `agent.py` (task 5) and no
limit resolution from `sa_keys.max_concurrent_calls` (task 4).

Design (see docs/superpowers/plans/2026-07-16-credential-rate-limit.md):
`credential_slots(id, credential, slot_index, pc_id, acquired_at)` has NO
SQLAlchemy model (migration 0047 comment) — every access here is raw SQL via
``sqlalchemy.text()``. A row is a held "slot" against a credential
fingerprint (see ``credential_id.credential_for``); only *fresh* rows
(younger than ``STALE_TTL_SECONDS``) count against the caller-supplied
``limit``.

ONE LOCK PER SLOT, NOT ONE LOCK PER CREDENTIAL
----------------------------------------------
This used to serialize every acquisition per credential via
``pg_advisory_xact_lock(hashtext(credential))``, holding that lock across a
count-then-insert. Measured in production on a 38-host fleet that shares one
Gemini key — so every host hashed to the SAME lock: 75 database connections
blocked on it, longest wait 822 seconds, while only 54 of a 900-slot ceiling
were in use. The limiter throttled nothing and blocked everything; blocked
workers could not even heartbeat and dropped out of the worker roster.

The critical section was the real defect, not its width: it spanned four
client round trips (BEGIN, lock, count, insert, COMMIT), so the fleet's whole
acquisition rate was capped at one-per-round-trip-latency — and that latency
degrades exactly when the fleet is busy.

So the mutual exclusion is now sharded all the way down to one lock per
*slot*, and each of those locks is a unique-index entry rather than an
advisory lock: ``UNIQUE(credential, slot_index)`` (migration 0060) makes it
physically impossible for more than ``limit`` rows to exist for a credential
across the index range ``[0, limit)``. ``acquire`` picks a free index at
random and claims it in ONE statement, so:

- **The ceiling is exact, not approximate.** It is enforced by the unique
  index, not by a count the caller races against. Verified against a real
  Postgres at limits 1/2/8/25 with 60 truly-concurrent acquirers: never one
  row over. (A plain ``INSERT ... SELECT ... WHERE (count) < :limit`` is NOT
  race-free at READ COMMITTED — each statement's snapshot predates its
  concurrent peers' commits, so the same probe overshot limit=1 to NINE rows.
  Taking the advisory lock *inside* that one statement is worse still — the
  statement's snapshot is taken BEFORE the lock is granted, so every waiter
  reads the same pre-lock state and all 40 acquirers inserted.)
- **Nothing serializes fleet-wide.** No advisory lock is taken at all. Two
  acquirers collide only when they randomly pick the SAME free index, and
  that collision is resolved inside a single statement.

Each poll iteration in ``acquire`` opens and closes its OWN short session,
runs exactly one statement in AUTOCOMMIT (no BEGIN/COMMIT round trips), and
never holds it across the inter-poll sleep — so a waiting worker occupies a
pool connection for one round trip per poll rather than for as long as it is
queued. That matters: worker processes run a 2+2 connection pool
(``db._pool_config``), the same pool the actual jobs need.

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

# A claim attempt reports WHY it failed, which lets us tell the two cases
# apart instead of sleeping through both:
#   * no free slot index at all  -> the credential really is saturated; sleep.
#   * a free index existed but a concurrent acquirer took it first -> retry
#     straight away, because capacity almost certainly still exists.
# Only the second case burns an immediate retry, and only this many per poll,
# so a genuinely saturated credential still costs exactly one statement per
# poll. Measured collision rate with 40 concurrent acquirers against a
# 900-slot ceiling: ~4% of attempts — cheap to re-try, needlessly expensive
# to sleep a full second over.
_RACE_RETRIES_PER_POLL = 2

SlotId = Union[UUID, object]

# ONE statement, so the whole critical section is server-side: no advisory
# lock, and nothing is held across a client round trip.
#
# `cand` picks one slot index that is free *right now* (no fresh row holds
# it). MATERIALIZED is load-bearing twice over: it pins `random()` to a
# single draw, and it keeps the `EXISTS` in the final SELECT reporting on the
# same draw the INSERT used.
#
# The second qual in `cand` — total fresh rows < limit — is what keeps the
# contract exact when a ceiling is LOWERED while calls are in flight: rows
# parked at an index >= the new limit are invisible to the per-index NOT
# EXISTS, so without this they would not hold new admissions back. It can
# never over-admit (the unique index is the hard bound); at worst it costs a
# retry.
#
# ON CONFLICT DO UPDATE is the stale-slot takeover: if the index we picked
# turns out to be held by a row that has aged past the TTL (a holder that
# crashed without releasing), we steal it. Rotating `id` on that steal is
# deliberate — the previous holder's `release(old_id)` must not delete the
# row now owned by someone else. That holds even when its DELETE was already
# in flight: READ COMMITTED re-checks `id = :old` against the UPDATED row
# version and matches nothing (verified against a real Postgres).
# If the conflicting row is still fresh, the WHERE fails, no row comes back,
# and we simply lost the race for that index.
#
# Cost is linear in the ceiling (the candidate scan walks `[0, limit)`), which
# is free at the ceilings this system actually runs: measured 0.4ms at
# limit=8 and 0.3ms at limit=900 with 890 held. It only becomes worth caring
# about at absurd values — ~1.7ms at 10k, ~11ms at 100k.
_ACQUIRE_SQL = text(
    """
    WITH cand AS MATERIALIZED (
        SELECT g.i AS slot_index
          FROM generate_series(0, :limit - 1) AS g(i)
         WHERE NOT EXISTS (
                SELECT 1 FROM credential_slots s
                 WHERE s.credential = :cred
                   AND s.slot_index = g.i
                   AND s.acquired_at > now() - make_interval(secs => :ttl))
           AND (SELECT count(*) FROM credential_slots s2
                 WHERE s2.credential = :cred
                   AND s2.acquired_at > now() - make_interval(secs => :ttl)) < :limit
         ORDER BY random()
         LIMIT 1
    ), claimed AS (
        INSERT INTO credential_slots (credential, slot_index, pc_id)
        SELECT :cred, cand.slot_index, :pc_id FROM cand
        ON CONFLICT (credential, slot_index) DO UPDATE
           SET pc_id = EXCLUDED.pc_id,
               acquired_at = now(),
               id = gen_random_uuid()
         WHERE credential_slots.acquired_at <= now() - make_interval(secs => :ttl)
        RETURNING id
    )
    SELECT (SELECT id FROM claimed) AS slot_id,
           EXISTS (SELECT 1 FROM cand) AS had_free_slot
    """
)


async def _try_claim(
    credential: str, limit: int, pc_id: str
) -> tuple[Optional[UUID], bool]:
    """One claim attempt. Returns ``(slot_id_or_None, had_free_slot)``.

    AUTOCOMMIT, not ``session.begin()``: the claim is a single statement and
    is therefore already atomic on its own, so an explicit transaction would
    only add BEGIN and COMMIT round trips — tripling how long a *waiting*
    worker holds one of the four connections its process has.
    """
    async with SessionLocal() as session:
        conn = await session.connection(
            execution_options={"isolation_level": "AUTOCOMMIT"}
        )
        row = (
            await conn.execute(
                _ACQUIRE_SQL,
                {
                    "cred": credential,
                    "pc_id": pc_id,
                    "ttl": STALE_TTL_SECONDS,
                    "limit": limit,
                },
            )
        ).one()
    return row.slot_id, row.had_free_slot


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

    Each poll is ONE statement in its own short-lived session — no advisory
    lock, nothing held across a client round trip, and nothing held across
    the inter-poll sleep. Sleeps between polls with exponential backoff
    (``1s`` -> ``5s``, doubling each miss, + jitter, clipped to the remaining
    budget). A poll that lost a race for a free slot index (rather than
    finding the credential saturated) retries immediately instead of
    sleeping — see ``_RACE_RETRIES_PER_POLL``.
    """
    if not limit or limit <= 0:
        return BYPASS

    deadline = time.monotonic() + wait_budget_s
    poll_interval = _POLL_INTERVAL_MIN_S
    while True:
        for _ in range(_RACE_RETRIES_PER_POLL + 1):
            slot_id, had_free_slot = await _try_claim(credential, limit, pc_id)
            if slot_id is not None:
                return slot_id
            if not had_free_slot:
                break  # genuinely saturated — wait for someone to release

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
