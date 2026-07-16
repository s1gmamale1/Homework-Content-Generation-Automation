# Fleet-wide per-credential api concurrency limiter (BE-16 / concurrency-knob-1 Phase 2)

**Item:** audit BE-16 — concurrency limits are per-process, not per-credential across the fleet.
Live incident 2026-07-16: 12 workers on one Vertex account drove **92–134 concurrent api calls**
(reconstructed from `agent_usages` started/completed stamps) → 473 rate-limit errors in 2 hours;
the shipped reactive backoff (Phase 1, `agent._spawn` retry loop) absorbs residue but cannot
prevent the stampede. Operator moves to 8 workers/account, but 8 workers × `AGENT_MAX_CONCURRENCY=8`
still allows 64 concurrent calls per account.

## Approach & key decisions (locked with user 2026-07-16, data-backed)

- **Semantics: fleet-wide ceiling on CONCURRENT api calls per credential** (not RPM — no verified
  quota numbers exist; measured: fresh accounts 429'd at 1–4 in-flight while the old memory's
  "16-21" was job-level on an aged account). Reactive backoff stays as the correction layer.
- **On saturation: wait for a slot.** Measured slot-holds (7d api rows): content p50 29s / judge 43s
  / solver 15s / extract 31s, p90 53–70s → at 8 slots a slot frees every ~4s steady-state; waits are
  seconds. Wait is bounded by the remaining per-attempt budget (`per_attempt_timeout_seconds=600`).
  Rejected fail-fast-transient: burns retries for sec-scale waits.
- **Scope: ALL api providers** — credential = Vertex SA project (gemini), else key fingerprint
  (gemini AI-Studio / claude / clodex). Clodex's published 60 RPM per key lands in the same machinery.
- **Config: env default per provider + per-key DB override** — measured per-account variance
  (fresh vs aged Vertex quotas) makes one global number wrong by construction.
  `CREDENTIAL_MAX_CONCURRENT_GEMINI=8` (locked default) / `_CLAUDE=8` / `_CLODEX=8`; `0` = limiter
  off for that provider. Override: nullable `sa_keys.max_concurrent_calls` (migration **0047**),
  editable in the SA-keys panel — tune weak accounts down / proven accounts up, no restarts.
- **Mechanism: Postgres slot table** (the fleet's only shared infra; same doctrine as the queue/bus).
  `credential_slots(id, credential, pc_id, acquired_at)`; acquire = `pg_advisory_xact_lock(hashtext(credential))`
  + count-then-insert in one tx; release = DELETE by id in `finally`; crash leak = rows older than
  `per_attempt_timeout_seconds` ignored by the count and deleted by the existing worker sweep.
  **DB is touched only at acquire/release (two short transactions per call) — never held during the
  model call.**
- **Fail-open, loudly:** limiter DB errors must never stop generation — log ERROR
  (`credential limiter: BYPASSED`) and proceed uncapped. Flagged for gate.
- **Wire point:** the api branch of `agent._spawn_once` (`agent.py:511`), INSIDE the local
  `_semaphore()` — a process waiting on a fleet slot holds one local slot; acceptable (cli retired,
  local sem is process-protection only). Head TOC extraction passes the same choke point → limited too.
- Verified seams: `_spawn_once` api branch `agent.py:503-515`; per-process sem `agent.py:218-242`;
  worker sweep loop `worker.py:656-664`; SA key sync `worker.py:266` + `sa_key_apply`; assignments
  `sa_key_assignments` (hostname→key_id); `_meta` payload `sa_keys.py:19`; no PATCH endpoint exists
  yet on sa-keys. #93 merged (`ebe3f74`) — no worker.py collision; notion lane owns notion_fetch/books,
  no overlap. Migration numbering: 0046 is head → **0047**. Worklog **0142** (0141 = notion lane).
- Branch `feat/credential-rate-limit`, worktree `../HCGA-cred-limit`. Suite baseline: re-run in
  worktree (last clean: 1572/217). Scratch DB `edu_scratch_credlim` for RED bites-proofs.

## Tasks (TDD per task, commit each; stage only listed files)

### Task 1 — migration 0047: slots table + per-key override column

`alembic/versions/0047_credential_slots.py`: `credential_slots(id uuid pk default gen_random_uuid(),
credential text not null, pc_id text not null, acquired_at timestamptz not null default now())`
+ `ix_credential_slots_credential`; `sa_keys` + `max_concurrent_calls int NULL`.
Test (`tests/migrations/`, scratch-DB pattern): upgrade head → both present; downgrade → gone.
Commit: `feat(limiter): migration 0047 — credential_slots + sa_keys.max_concurrent_calls (BE-16 task 1)`

### Task 2 — credential identity (pure) (RED → GREEN)

`app/services/credential_id.py`: `credential_for(provider: str, env: Mapping) -> str | None` —
gemini: `f"gemini:{GOOGLE_CLOUD_PROJECT}"` when Vertex SA pair present, else
`f"gemini:{sha256(GEMINI_API_KEY)[:16]}"`; claude/clodex: `f"{provider}:{sha256(key)[:16]}"`;
no credential env → None (limiter skips). Tests: per-provider mapping; **no raw key material in the
output** (assert key substring absent); None paths; deterministic.
Commit: `feat(limiter): credential fingerprint per provider (BE-16 task 2)`

### Task 3 — limiter core (RED → GREEN, scratch DB)

`app/services/credential_limiter.py`:
- `async acquire(credential, limit, *, pc_id, wait_budget_s) -> slot_id | None` — loop: one tx
  (`pg_advisory_xact_lock(hashtext(:cred))` → `SELECT count(*) WHERE credential=:cred AND
  acquired_at > now()-interval '<per_attempt_timeout>s'` → if < limit INSERT RETURNING id);
  else sleep 1s+jitter and retry until `wait_budget_s` exhausted → None.
- `async release(slot_id)` — DELETE; missing row is a no-op.
- `async sweep(session) -> int` — DELETE stale rows (leaked by crashes); returns count.
**RED bites-proofs** (`tests/integration/test_credential_limiter.py`, RUN_DB_INTEGRATION,
`edu_scratch_credlim`): limit=1 + two concurrent acquires → exactly one wins, second waits then
wins after release; second times out (small budget) → None; stale row (backdated acquired_at)
does NOT count and sweep deletes it; limit=0/None → acquire returns sentinel BYPASS without touching DB.
Commit: `feat(limiter): postgres slot limiter — acquire/release/sweep (BE-16 task 3)`

### Task 4 — limit resolution: per-key override → provider default (RED → GREEN)

`config.py`: `credential_max_concurrent_gemini: int = 8`, `_claude: int = 8`, `_clodex: int = 8`.
`credential_limiter.resolve_limit(session, provider, hostname) -> int` — hostname's
`sa_key_assignments` row → `sa_keys.max_concurrent_calls` when set (gemini/SA only), else the
provider env default. Cache per (provider, hostname) ~60s TTL so per-call cost is zero.
Tests: override wins; NULL override → default; non-SA providers → default; TTL refresh honors a
changed override without restart.
Commit: `feat(limiter): per-key override with provider-default fallback (BE-16 task 4)`

### Task 5 — wire into the api spawn path + sweep (RED → GREEN)

`agent._spawn_once` api branch: compute `credential_for(provider.name, os.environ)`; resolve limit;
`slot = await acquire(...)` (wait budget = remaining per-attempt time; own short DB session);
`try: api_transport.generate(...) finally: release(slot)`. DB error anywhere → log
`credential limiter: BYPASSED (<err>)` + proceed. `worker.py` periodic sweep gains
`credential_limiter.sweep` (piggyback on the existing stale-jobs sweep site `worker.py:656-664`).
Tests (`tests/services/test_agent_limiter_wiring.py`, monkeypatched api_transport + limiter):
acquire happens before generate and release after, **including on exception**; None credential →
no acquire; BYPASS on DB error still calls generate; cli spawns never touch the limiter.
Commit: `feat(agent): api calls acquire fleet credential slots (BE-16 task 5)`

### Task 6 — API + FE: override editing + visibility

`PATCH /api/v1/sa-keys/{key_id}` accepting `{max_concurrent_calls: int|null}` (validate ≥1 or null;
`_meta` gains the field). `GET /api/v1/sa-keys` rows + per-credential `slots_in_use` (one grouped
count over live slot rows). FE (`sa-keys-panel.tsx` + types/api): numeric input per key row
(blur→PATCH, toast), `in-flight N/limit` text next to the status column. tsc + build + tsx pure
tests only if logic is extracted.
Commit: `feat(sa-keys): per-key concurrency override + in-flight visibility (BE-16 task 6)`

### Task 7 — docs + acceptance + finish

- **Acceptance (real, bounded, ~$0.01):** on the head with the live gemini credential, set the
  env default to 2 (env var for the process only), fire **6 concurrent tiny `agent.run_phase`
  gemini api calls** (5-token prompts); prove from `credential_slots` history + call timestamps
  that in-flight never exceeded 2 and all 6 complete. Paste evidence in the PR. Restore env.
- Docs de-stale: CLAUDE.md (worker/limits bullet), `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`,
  `docs/DATABASE.md` (new table + column), `.env.example`.
- Close BE-16 in root `Wishlist.md` + `concurrency-knob-1` Phase-2 note in `docs/memory/WISHLIST.md`.
- Worklog **0142** + INDEX row (re-verify tail at finish). Full suite; rebase check; push;
  PR → **GK2 gates + merges**; plan → `shipped/`.

## Flagged for the gate

1. **Fail-open on limiter DB errors** (loud ERROR, generation proceeds uncapped) — availability
   over enforcement; the alternative (fail-closed) turns a head-DB blip into a fleet-wide stall.
2. Fleet-slot wait holds the local semaphore slot (simplest correct shape; cli lane retired).
3. Slot wait consumes the per-attempt timeout budget — a fully saturated account can push long
   calls toward the 600s bound; acceptable at measured hold times, revisit if p90 grows.
4. Default ceilings (8/8/8) are operator-tunable starting points, not verified quotas — the
   per-key override is the tuning instrument; reactive backoff remains the net.
5. `sa_keys.max_concurrent_calls` applies only to SA-assigned gemini credentials; env-key
   credentials (claude/clodex/AI-Studio) use provider defaults only (no DB identity to hang an
   override on — documented).
