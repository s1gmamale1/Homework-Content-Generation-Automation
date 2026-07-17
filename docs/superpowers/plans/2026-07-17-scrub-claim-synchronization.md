# Scrub-vs-claim host synchronization (PR #101 gate round 3, finding 1 correction)

## Approach & key decisions

**Problem (gate round 3, all three sub-claims verified in code).** My round-2 finding-1 fix
(`count_running_for_host` gating the scrub clear) is a **read-only snapshot, not synchronization**:
1. TOCTOU — a sibling can claim a job in the window between the count returning 0 (`jobs.py:529`
   claim commits after the count) and the credential files being deleted.
2. No claim-gate — `_sync_sa_key` can *defer* the scrub, then the main loop proceeds straight to
   `_claim_one` (`worker.py:301`) and claims anyway; the slot-wait path (`worker.py:296-301`) can
   also receive a scrub mid-wait and claim without re-syncing. A pending revoke never converges
   because the host keeps taking new work.
3. `count_running_for_host` counts only `running`; a `cancelling` job still has a live pipeline
   task (+ credential use) until its heartbeat cancels and finalizes it (`count_active_for_book`
   already treats `pending`/`running`/`cancelling` as active — the precedent).

**Fix — the BE-02 advisory-lock pattern, host-scoped** (shared at claim, exclusive at scrub-clear,
state re-read after the lock; mirrors `books_repo.lock_book_shared/exclusive`). No migration.

- **Host locks** `workers_repo.lock_host_shared/exclusive(session, hostname)` on
  `hashtext(f"host:{hostname}")` (xact-scoped `pg_advisory_xact_lock_shared` /
  `pg_advisory_xact_lock`) — copy of the book-lock helpers, host key namespace.
- **Claim-gate in `_claim_one`** (NOT in `claim_next_job` — keeps its many SQL-capture/fake-session
  tests untouched, and `self.hostname` is natural at the worker). At the very top of the existing
  `async with session.begin():` claim tx, BEFORE the budget read: take `lock_host_shared`, then
  `if await sa_keys_repo.scrub_pending_for_host(session, self.hostname): return None`. The shared
  lock is held through `claim_next_job`'s `SELECT … FOR UPDATE` + the UPDATE-to-running, released
  on the tx commit — so the scrub's exclusive lock cannot interleave with a claim, and the
  scrub-pending state is re-read under the lock. Concurrent claims (all shared) never block each
  other — no hot-path throughput regression; only a rare exclusive scrub blocks them.
- **Tombstone writers take the exclusive host lock (gate round-3 corr. 2).** The advisory lock
  only serializes participants who take it — so `POST /scrub` (and, symmetrically, `assign` and
  `unassign`) MUST take `lock_host_exclusive` before mutating `sa_key_assignments`, or a claim
  that already re-read "no tombstone" under its shared lock proceeds and creates a replacement
  after the scrub commits. All three assignment-state routes (`sa_keys.py` assign/unassign/scrub)
  take the exclusive host lock at the top of their tx. This also stops assign/unassign from
  interleaving with a `_scrub_if_idle` clear that re-read the old tombstone.
- **Scrub-clear synchronization in `_scrub_if_idle`**: takes `lock_host_exclusive` on the session's
  **already-open** tx (see the tx-structure fix below), then **re-reads** under the lock:
  `scrub_pending_for_host` still true, `count_active_for_host == 0`, and `not self._tasks` (local),
  THEN clears. The exclusive lock waits for every in-flight claim tx (shared) to commit, so the
  active-count it reads is authoritative; and because the claim-gate refuses new claims while the
  tombstone stands, the drain is monotonic — once active hits 0 it stays 0. The file clear runs
  before the tx commits so the lock is held through it.
- **Transaction structure (gate round-3 corr. 1).** `get_assignment_with_key`'s SELECT autobegins
  the session's tx, so `_scrub_if_idle` must NOT open its own `session.begin()` (raises "A
  transaction is already begun"). The explicit `begin()` moves OUT to the caller `_sync_sa_key`,
  wrapping the read + scrub; `_scrub_if_idle` reuses that tx (lock + re-read + clear, no new
  begin). The apply path stays OUTSIDE that block (after the session closes) so its long
  `to_thread` key-pull never holds a DB connection.
- **Lock ordering.** The host advisory lock never coincides with the BE-02 book/section locks in
  one tx (claims take host locks + job-row locks; activation paths take book locks; the two never
  overlap), so no new global ordering constraint. Within the host key, order is always
  advisory-lock-first — no cycle with the row locks taken afterward.
- **`count_running_for_host` → `count_active_for_host`**: status in (`running`, `cancelling`),
  matching `count_active_for_book`.

**Explicit guarantees (gate round-3 follow-up — all satisfied by the design above, called out so
they're tested, not assumed):**
- **In-flight jobs are NEVER interrupted.** Scrub does not cancel or kill a running/cancelling
  pipeline — it only blocks *replacements* (claim-gate) and waits for the existing jobs to
  finish/cancel on their own (`count_active_for_host == 0`) before clearing. A revoke is a drain,
  not a kill.
- **Claim-vs-scrub is serialized by the host advisory lock** (shared at claim, exclusive at
  clear, same `host:{hostname}` key) — they cannot interleave.
- **The claim side re-reads `scrub_requested_at` AFTER acquiring the shared lock** (via
  `scrub_pending_for_host` inside the locked claim tx), never off a pre-lock snapshot.
- **UI (Task 4):** the parked state is explicit — label `SCRUB REQUESTED · HOST PARKED`; and
  **Unassign on a tombstoned host warns** that dismissing the revoke lets the host claim jobs
  again with NO service-account key.

**Decision flagged for the gate — "park until dismissed" semantic.** While a scrub tombstone
stands (`scrub_requested_at IS NOT NULL`), the host's workers claim **nothing** (all providers,
not just gemini). This is required for drain convergence and is the intended revoke semantic: a
host pending-revoke takes no new work. It stays parked until the operator **Unassign**s (deletes
the row) or **assign**s a new key (`repo.assign` sets `scrub_requested_at=None`) — both already
wired in the panel. Rejected the narrower "block only until cleared, then resume": the schema has
only `scrub_requested_at` (set = pending revoke); a "cleared but still tombstoned" third state
would need a new column for no operational gain (a revoked host with no key can't do api work
anyway, and the operator dismisses in one click).

**Load-bearing facts verified:** claim runs in a real tx (`worker.py:418` `async with
session.begin()`); `claim_next_job` has 12+ callers incl. fake-session SQL-capture tests — leaving
its signature untouched avoids that blast radius; `_sync_sa_key`'s session has no `begin()` today
(scrub path will add one); `sa_key_assignments` upsert nulls `scrub_requested_at` on assign
(`sa_keys.py:145-152`) and deletes the row on unassign (`:155-159`); `count_active_for_book`
(`jobs.py:823`) is the running+cancelling precedent.

---

## Task 1 — Host-lock primitives + scrub-pending + count rename (TDD, real-DB)

**Files:** `app/repositories/workers.py` (+`lock_host_shared`/`lock_host_exclusive`),
`app/repositories/sa_keys.py` (+`scrub_pending_for_host`), `app/repositories/jobs.py`
(rename `count_running_for_host`→`count_active_for_host`, add `cancelling`),
`tests/integration/test_count_running_for_host.py`→`test_host_scrub_sync.py` (rename + extend),
worker.py + test call-sites updated for the rename.

1. **RED** — new `tests/integration/test_host_scrub_sync.py` (real Postgres, `RUN_DB_INTEGRATION`
   guard, scratch `edu_scratch_scrub`):
   - `count_active_for_host` counts `running` AND `cancelling` sibling pids, excludes done/other
     host/prefix-host (extend the existing proven cases with a `cancelling` row).
   - `scrub_pending_for_host` — True with a `scrub_requested_at` row, False with a keyed
     assignment, False with no row.
   - **Two-connection lock race** (deterministic via try-locks, winner-conditional oracle like
     BE-02): conn A `session.begin()` + `lock_host_shared` (uncommitted); conn B
     `pg_try_advisory_xact_lock(hashtext('host:H'))` (exclusive try) returns **False** (blocked);
     after A commits, B's try returns **True**. And the mirror: B holds exclusive → A's
     `pg_try_advisory_xact_lock_shared` returns False.
2. **GREEN** — implement the two lock helpers (copy book-lock docstrings, host key namespace),
   `scrub_pending_for_host` (EXISTS on `scrub_requested_at IS NOT NULL`), rename the count fn +
   add `cancelling`. Update the rename's callers (`worker.py`, `test_worker_sa_key_sync.py`
   autouse fixture patch target).
3. Run: `RUN_DB_INTEGRATION=1 DATABASE_URL=…edu_scratch_scrub uv run python -m pytest
   tests/integration/test_host_scrub_sync.py -q`, plus `uv run python -m pytest
   tests/services/test_worker_sa_key_sync.py -q`.
4. Commit: `feat(repo): host advisory locks + scrub_pending_for_host + count_active_for_host`.

## Task 2 — Assignment-state writers take the exclusive host lock (TDD, real-DB)

**Files:** `app/api/v1/sa_keys.py` (assign/unassign/scrub routes), `tests/api/test_sa_keys_assign_api.py`
or a new `tests/integration/test_assignment_writer_locks.py`.

1. **RED** — real-DB two-connection test: conn A holds `lock_host_shared('H')` (uncommitted,
   simulating an in-flight claim tx); the scrub route's tombstone write (conn B) must not be able
   to grab the exclusive lock until A commits — prove via `pg_try_advisory_xact_lock` that B's
   exclusive attempt returns False while A holds shared, True after. And a source/behavior check
   that each of the three routes calls `lock_host_exclusive` before its `repo.*` mutation.
2. **GREEN** — `sa_keys.py`: `from app.repositories import workers as workers_repo`; in
   `assign_sa_key`, `unassign_sa_key`, `scrub_sa_key`, add
   `await workers_repo.lock_host_exclusive(session, hostname)` BEFORE the first mutation (for
   assign, before `repo.assign`; the 404 `repo.get` may stay before the lock — it touches no host
   state). The route's own `session.commit()` releases the xact lock.
3. Run the new test + `tests/api/test_sa_keys_assign_api.py` `tests/api/test_sa_keys_api.py`
   (the ~mocked route tests — confirm the added `execute` for the lock doesn't break their session
   mocks; if the api conftest mocks the lock like BE-02 did, mirror that no-op).
4. Commit: `feat(sa-keys): assignment-state writers take the exclusive host lock`.

## Task 3 — Claim-gate + real scrub-write-vs-claim race (TDD, real-DB)

**Files:** `app/services/worker.py` (`_claim_one`), `tests/integration/test_scrub_claim_gate.py` (new).

1. **RED** — real-DB: seed host `H`, a pending claimable cli job, and a scrub tombstone; drive
   `Worker._claim_one` (real session, `self.hostname='H'`) → returns None (refused); delete the
   tombstone → it claims. **Real two-connection scrub-write-vs-claim race** (gate corr. 2 — drive
   the ACTUAL tombstone write, not raw primitives): winner-conditional oracle —
   • scrub-writes-first: conn B runs the real `scrub` route/repo write (takes exclusive lock,
     commits the tombstone); THEN conn A's `_claim_one` (shared lock, re-read) refuses and NO job
     flips to `running`.
   • claim-first: conn A's `_claim_one` claims (job→running) and commits; the scrub write's
     exclusive lock waited, tombstone lands after; a subsequent `_scrub_if_idle` sees `active==1`
     and defers. (Any-of assertions BANNED — assert the exact winner path.)
2. **GREEN** — in `_claim_one`, at the top of `async with session.begin():` before the budget
   read: `await workers_repo.lock_host_shared(session, self.hostname)`; `if await
   sa_keys_repo.scrub_pending_for_host(session, self.hostname): return None`. (`sa_keys_repo`
   already imported in worker.py.)
3. Run the new test + `tests/services/test_worker_cooldown.py`
   `tests/services/test_worker_version_gate.py` (assert `_claim_one`'s claim/no-claim mocks still
   hold) + `tests/repositories/test_fleet_pause_gate.py`.
4. Commit: `feat(worker): scrub tombstone blocks this host's claims (shared host lock + re-read)`.

## Task 4 — Scrub-clear waits for host drain under the exclusive lock (TDD)

**Files:** `app/services/worker.py` (`_sync_sa_key` tx restructure + `_scrub_if_idle`),
`tests/services/test_worker_sa_key_sync.py`, `tests/integration/test_scrub_drain.py` (new real-DB).

1. **RED** —
   - Unit (mocked): extend the autouse fixture so `count_active_for_host`→0 and
     `scrub_pending_for_host`→True by default; add `test_scrub_defers_until_host_drained` —
     `count_active_for_host`→1 (a sibling's running/cancelling job) with THIS process idle → assert
     no clear (the host-wide drain gate, replacing the round-2 `count_running_for_host` monkeypatch).
     Keep the malformed-.env best-effort test. **The `_FakeSession` must tolerate the caller's
     `async with session.begin()`** — give it a `begin()` returning an async-ctx no-op (or an
     `AsyncMock`), since the tx boundary now lives in `_sync_sa_key`.
   - Real-DB `test_scrub_drain.py`: with a `cancelling` sibling job present the clear defers under
     the exclusive lock; with none it proceeds and clears the residue.
2. **GREEN** —
   - `_sync_sa_key`: restructure per corr. 1 — `async with SessionLocal() as session: async with
     session.begin():` wraps `get_assignment_with_key` + the `asg["scrub"]` branch calling
     `_scrub_if_idle(session)`; the apply path stays AFTER (outside the session, `asg` dict
     survives).
   - `_scrub_if_idle(session)`: after the residue + `self._tasks` gates, `await
     lock_host_exclusive(session, self.hostname)` on the passed (already-open) tx → re-read
     `scrub_pending_for_host` (still true) and `count_active_for_host == 0` → if either fails,
     return (defer, drops the whole tx); else clear files + rebind. NO `session.begin()` here.
3. Run `tests/services/test_worker_sa_key_sync.py` + `test_worker_startup_applies_key.py` + the
   real-DB drain test.
4. Commit: `fix(worker): scrub clears only under exclusive host lock after full drain`.

## Task 5 — UI: HOST PARKED label + Unassign warning (gate round-3 items 4-5)

**Files:** `web/src/components/fleet/sa-keys-panel.tsx`.

1. **Parked label** — the assignment-label cell renders `SCRUB REQUESTED` for a tombstoned row
   (round-2). Change it to `SCRUB REQUESTED · HOST PARKED` (same amber tint) so the operator sees
   the host is claiming nothing, not merely that a revoke was recorded.
2. **Unassign warning** — the Unassign button's onClick, WHEN the row is a scrub tombstone
   (`a?.scrub`), goes through a `window.confirm` first: "This host is parked by a scrub (revoke).
   Unassigning dismisses the revoke and lets the host claim jobs again with NO service-account
   key. Continue?" — proceed only on confirm. A normal keyed Unassign (`a?.key_id`, not a
   tombstone) keeps today's no-confirm behavior. (`window.confirm` matches the house pattern used
   by book-delete / batch cancel-all.)
3. No pure-logic module needed (conditional-confirm + label string); gates
   `cd web && npx tsc -p tsconfig.app.json --noEmit` && `npm run build`; re-run
   `npx tsx src/lib/sa-key-hosts.test.ts` (no regression).
4. Commit: `feat(web): SCRUB REQUESTED · HOST PARKED label + keyless-claim warning on Unassign`.

## Task 6 — Finish

1. Full suite `uv run python -m pytest tests/ -q`; the real-DB tests against `edu_scratch_scrub`.
2. Rebase check `git fetch origin && git log HEAD..origin/Nggaev-v2`.
3. Worklog 0147 **round-3 addendum** (MASTER_MEMORY + INDEX row already exist — append the
   synchronization correction); note the "park until dismissed" semantic in `docs/CODE_MAP.md`
   (claim-gate) and the SA-keys panel section.
4. Push to `feat/sa-key-dead-host` (updates PR #101). `git mv` this correction plan →
   `docs/superpowers/plans/shipped/` (the parent lane's plan already shipped there in worklog 0147).

**Operator note (unchanged + new):** a host with a standing `SCRUB REQUESTED` tombstone now claims
NO jobs until the operator Unassigns it or assigns a new key — parked by design, visible via the
panel chip.
