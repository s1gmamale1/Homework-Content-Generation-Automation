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
- **Scrub-clear synchronization in `_scrub_if_idle`**: wrap the check-and-clear in
  `async with session.begin():` → `lock_host_exclusive` → **re-read** under the lock:
  `scrub_pending_for_host` still true, `count_active_for_host == 0`, and `not self._tasks` (local),
  THEN clear. The exclusive lock waits for every in-flight claim tx (shared) to commit, so the
  active-count it reads is authoritative; and because the claim-gate refuses new claims while the
  tombstone stands, the drain is monotonic — once active hits 0 it stays 0. The file clear runs
  inside the same tx block so the lock is held through it.
- **`count_running_for_host` → `count_active_for_host`**: status in (`running`, `cancelling`),
  matching `count_active_for_book`.

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

## Task 2 — Claim-gate: scrub tombstone stops the host from claiming (TDD, real-DB)

**Files:** `app/services/worker.py` (`_claim_one`), `tests/integration/test_scrub_claim_gate.py` (new).

1. **RED** — real-DB test: seed a host `H`, a pending claimable cli job, and a scrub tombstone for
   `H`; drive `Worker._claim_one` (real DB session, `self.hostname='H'`) → returns None (refused).
   Delete the tombstone → `_claim_one` claims the job. Plus a **two-connection claim-vs-scrub
   race**: conn B holds `lock_host_exclusive('H')` mid-scrub (uncommitted); conn A's `_claim_one`
   attempts its shared lock and cannot proceed until B commits, after which it re-reads the
   tombstone and refuses (winner-conditional: scrub-wins → claim refused + no job flips to
   running; claim-wins-first → the job is running and the scrub's exclusive waits, then sees
   active==1 and defers).
2. **GREEN** — in `_claim_one`, at the top of `async with session.begin():` before the budget
   read: `await workers_repo.lock_host_shared(session, self.hostname)`; `if await
   sa_keys_repo.scrub_pending_for_host(session, self.hostname): return None`. (Import
   `sa_keys_repo` — already imported as `sa_keys_repo` in worker.py.)
3. Run the new test + `tests/services/test_worker_cooldown.py`
   `tests/services/test_worker_version_gate.py` (they assert `_claim_one`'s claim/no-claim paths —
   confirm the added gate doesn't break their mocks) + `tests/repositories/test_fleet_pause_gate.py`.
4. Commit: `feat(worker): scrub tombstone blocks this host's claims (shared host lock + re-read)`.

## Task 3 — Scrub-clear waits for host drain under the exclusive lock (TDD)

**Files:** `app/services/worker.py` (`_scrub_if_idle`), `tests/services/test_worker_sa_key_sync.py`,
`tests/integration/test_scrub_drain.py` (new real-DB).

1. **RED** —
   - Unit (mocked): extend the autouse fixture so `count_active_for_host`→0 and
     `scrub_pending_for_host`→True by default; add `test_scrub_defers_until_host_drained` —
     `count_active_for_host`→1 (a sibling's running/cancelling job) with THIS process idle → assert
     no clear (the host-wide drain gate, replacing the round-2 `count_running_for_host` monkeypatch).
     Keep the malformed-.env best-effort test (now the clear path runs inside `session.begin()`).
   - Real-DB `test_scrub_drain.py`: exclusive-lock-held re-read — with a `cancelling` sibling job
     present the clear defers; with none it proceeds.
2. **GREEN** — `_scrub_if_idle`: after the residue + `self._tasks` gates, open
   `async with session.begin():` → `lock_host_exclusive(session, self.hostname)` → re-read
   `scrub_pending_for_host` (still true) and `count_active_for_host == 0` → if either fails,
   return (defer); else clear the files + rebind, all inside the tx block. Replace the round-2
   `count_running_for_host > 0` check with this locked drain re-read.
3. Run `tests/services/test_worker_sa_key_sync.py` + the real-DB drain test.
4. Commit: `fix(worker): scrub clears only under exclusive host lock after full drain`.

## Task 4 — Finish

1. Full suite `uv run python -m pytest tests/ -q`; the real-DB tests against `edu_scratch_scrub`.
2. Rebase check `git fetch origin && git log HEAD..origin/Nggaev-v2`.
3. Worklog 0147 **round-3 addendum** (MASTER_MEMORY + INDEX row already exist — append the
   synchronization correction); note the "park until dismissed" semantic in `docs/CODE_MAP.md`
   (claim-gate) and the SA-keys panel section.
4. Push to `feat/sa-key-dead-host` (updates PR #101). No `git mv` (plan for the parent lane already
   shipped; this correction plan → `shipped/` at finish).

**Operator note (unchanged + new):** a host with a standing `SCRUB REQUESTED` tombstone now claims
NO jobs until the operator Unassigns it or assigns a new key — parked by design, visible via the
panel chip.
