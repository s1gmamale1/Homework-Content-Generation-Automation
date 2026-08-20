# Homework Generation Speed — Complete Modification Report

**Period covered:** fleet inception → 2026-08-20 (speed work concentrated 2026-08-13 → 2026-08-19; §0 covers the prehistory)
**Result:** from a fleet-wide maximum of **4 concurrent homeworks / ~0.2 finished per minute** to **196 concurrent workers on 37 hosts** (138+ measured concurrent, 8–9 finished per minute and still accelerating when last stopped), with **zero generation failures** in the final test runs.

Every modification below states the problem it solved, the exact change, and its measured effect. Modifications are grouped by layer.

---

## 0. Prehistory — how the fleet existed at all (before any speed work)

### 0.1 Provisioning classroom PCs into workers
- Ordinary school PCs were converted into fleet hosts entirely over MeshCentral:
  per-host admin accounts, git + uv installed, the repo materialized, `.env` seeded,
  and the worker wrapped in a Windows scheduled task ("HomeworkWorker") supervised
  to restart forever. Two supervisor defects that silently blocked adding new PCs
  were found and fixed — the reason the fleet was stuck small at first.
- Auto-provisioning daemons (`autoprovision`, then `fleetwatch`) made joining
  hands-free: any machine that boots is detected, joined, and serving within
  minutes; Wake-on-LAN sweeps run every 20 minutes. (Proof of maturity: on
  2026-08-20 two brand-new machines, Host-50/-69, were absorbed with zero human
  involvement, born on correct config.)

### 0.2 The 4-worker ceiling and the pool-boost lever (pre-deploy era)
- **Problem:** every host ran `WORKER_CONCURRENCY=4`, and the database pool was
  hardcoded 2+2 *at import time* — raising concurrency alone did nothing but pile
  jobs onto a 4-connection pool.
- **Interim fix (before any code could be shipped):** `worker_boost.py`, a startup
  shim that set `WORKER_CONCURRENCY=0` during import (tricking the pool into
  building 20+30) and then restored the real value for job slots. Its own bug —
  reading the environment when the value lives in `.env` — silently pinned hosts
  to 4 until an `.env` parser (utf-8-sig) was added.
- Per-host concurrency was then raised 4 → 12/8/4 by RAM tier: the fleet went from
  **4 simultaneous homeworks in total** (one host) to **252 configured slots**.
  The shim was retired ("unshimmed") once the deploy shipped real pool knobs.
- Model-call fan-out (`AGENT_MAX_CONCURRENCY`) tuned 24 → 16 → 12/8 for stability;
  the "4 hosts and 6 hosts" with config gaps were repaired so the whole fleet
  could participate.

### 0.3 Early platform decisions
- **pgbouncer chosen over raising Postgres `max_connections`** — the 16 GB head
  could not afford hundreds of raw backends; pooling was the sustainable answer
  (details in §2).
- **PDF pre-seeding** onto hosts for test books (until fetch-storm control shipped).
- **Reclaim window 120 → 600 s** so busy lessons stopped being stolen mid-run.
- **Windows 15-minute idle sleep disabled fleet-wide** (`powercfg`, Wake-on-LAN
  preserved) — machines stopped vanishing mid-day; live fleet grew 15 → 40.

### 0.4 The 260-lesson max-out test and fix waves (2026-08-14)
- First real load test: peak **80 concurrent on 19 hosts, ~5 % failures**.
  Every failure was root-caused into a written taxonomy (QueuePool exhaustion on
  unboosted hosts, empty-error deaths, connection-lost, fetch deaths, timeouts),
  field fixes applied and verified per host, and a recovery lap converted 52 prior
  failures. The burned-attempts bug (retried lessons arriving pre-exhausted) was
  fixed with an attempts reset on the retry/resume paths.
- Verdict of that era: field tuning was exhausted at a sustainable ~30–40
  concurrent; everything further required shipping code — which became the R27
  deploy (§1) and everything after it.

### 0.5 The R27 deploy mechanics (how the code fixes physically shipped)
- Full DB backup → fleet-wide FREEZE → push (first deploy from this codebase) →
  **canary host first**, which caught two real heartbeat bugs before the fleet saw
  them (§1.2) → staged adoption (idle hosts restarted, busy hosts adopted on
  natural exit).
- **Production migration under live load:** the standard migration deadlocked
  repeatedly against 40 claiming workers and ~21-hour zombie idle-in-transaction
  sessions; it landed as a hand-built single transaction taking ACCESS EXCLUSIVE
  locks on all touched tables in fixed order with a lock timeout and retry loop,
  after a zombie-session sweep. (The permanent guard — the 10-minute
  idle-in-transaction timeout — is §2.4.)
- **Offline code delivery for github-blocked hosts:** git bundle → base64 (the
  mesh file channel corrupts binaries) → upload → `certutil` decode → fetch/merge
  from the bundle. This converted Host-06, and later Host-40/-47 after reboots.

---

## 1. Worker code fixes (deployed to every host)

These shipped as the R27 deploy (commits `d27465f → 80df898/f20cb51`, migrations 0060–0062), after a 260-lesson load test exposed each one.

### 1.1 Credential limiter: global lock → per-slot claims
- **Problem:** every API call in the entire fleet took the *same* Postgres advisory lock before calling the model. All hosts serialized on one mutex: the fleet made 0.65 calls/second while machines sat idle (25 % utilisation).
- **Change:** replaced with per-slot rows (`credential_slots`, `UNIQUE(credential, slot_index)`) claimed in one autocommit statement — parallel admission instead of a single queue.
- **Effect:** call admission ~15–27× faster; the fleet stopped fighting itself.

### 1.2 Heartbeat overhaul (three fixes)
- **Problem:** workers were declared dead while working. (a) any worker could prune any peer's registry row, so losing a race evicted a *live* host (roster flapped 38→27→34→16); (b) the heartbeat shared the tiny DB pool with job traffic and starved; (c) the per-attempt budget (7.5 s) was smaller than a cold connection through the then-network (9–16 s), so the first beat could *never* succeed and each failure re-disposed the engine into permanent cold-start.
- **Change:** prune window 600 s→3600 s with a live-job anti-join; a dedicated heartbeat engine (its own 1+1 pool); attempt budget raised to 2×14 s per cycle; a one-time generous 60 s cold-start attempt per engine build.
- **Effect:** heartbeats stayed ≤31 s fresh *while hosts ran lessons*; the roster-flap class disappeared.

### 1.3 Claim fairness
- **Problem:** the claim query ordered by `order_index`, which is per-book — so one batch monopolized the whole fleet (one host held 12 lessons while 12 hosts held 1 each; other batches starved with 19 pending).
- **Change:** claim ordering by priority + per-lane service position across batches.
- **Effect:** even spread across hosts and batches; in the final runs 34–35 hosts all held work simultaneously.

### 1.4 Book-fetch storm control
- **Problem:** 24 workers each simultaneously downloaded the same 237 MB textbook (~5.7 GB over one link); waiters burned threads and starved *other* books; a quarter of the fleet produced nothing.
- **Change:** an async per-book lock taken before any thread, a wall-clock fetch budget (`BOOK_FETCH_TIMEOUT_SECONDS=600`, warn at 100 MB), and a real `BookFetchTimeout` failure instead of a silent stall. PDFs for test books were also pre-seeded onto hosts.
- **Effect:** fetch-death failure class went to zero and stayed there.

### 1.5 Transient-error classification for the SDK
- **Problem:** the Gemini SDK (httpx) network blips were classified as permanent → lessons died at attempt 1 of 3.
- **Change:** httpx/httpcore error signatures added to the transient list.
- **Effect:** blips retry instead of failing; connection-loss failures went to zero.

### 1.6 Reclaim accounting
- **Problem:** scheduling reclaims (host died, lesson re-queued) burned the lesson's *retry* budget — never-executed lessons arrived pre-exhausted ("attempts exhausted while pending").
- **Change:** a separate `reclaims` counter (migration 0060); reclaim window raised 120 s→600 s so live jobs are never stolen; the resume endpoint resets attempts.
- **Effect:** this failure class reduced to a single bookkeeping artifact across all subsequent runs.

### 1.7 Launch stagger
- **Problem:** launching 250+ lessons at second zero made every host storm the PDF endpoint and the model API simultaneously (the thundering herd that buried the network).
- **Change:** server-side staggered claim release — waves of 6/minute per batch.
- **Effect:** smooth ~15-minute ramps, no start-of-wave collapse; steady-state throughput unchanged.

### 1.8 Operator-sized DB pool knobs
- **Problem:** each worker's DB pool was hardcoded 2+2; under load hosts hit "QueuePool limit reached" — the single biggest failure signature of the first load test.
- **Change:** env knobs `WORKER_DB_POOL_SIZE` / `WORKER_DB_MAX_OVERFLOW`; fleet set to 6+4.
- **Effect:** QueuePool failures eliminated on every host running the new config.

---

## 2. Database & connection layer (head Mac)

### 2.1 pgbouncer (transaction pooling)
- **Problem:** each worker held direct Postgres connections; at 80 concurrent lessons backends hit 193 of the 250 hard maximum — the next step of scale would have locked the head out of its own database.
- **Change:** pgbouncer in transaction mode on `:6432`; workers repointed with `prepared_statement_cache_size=0` (asyncpg requirement). Config: `max_client_conn=6000`, `default_pool_size=100`, `max_db_connections=120`.
- **Effect:** hundreds of worker connections fold into ≤120 Postgres backends; Postgres headroom permanent.

### 2.2 pgbouncer file-descriptor ceiling
- **Problem:** macOS default gave the pgbouncer process 256 file descriptors — a hard ~250-connection wall regardless of config (its own log: "max expected fd use: 6412"). A 300-client probe knocked live workers' reconnects out for ~100 s.
- **Change:** pgbouncer moved from brew's service to a dedicated launchd service (`com.classa.pgbouncer`) with `NumberOfFiles=8192`; all restarts now via `launchctl kickstart` (a brew restart would regenerate the plist and silently restore the 256 limit).
- **Effect:** verified 223+ concurrent clients with zero refusals during the record run; full-fleet demand (~580) now has 14× headroom.

### 2.3 Wedge watchdog
- **Problem:** pgbouncer occasionally wedged (clients queued while idle server connections sat unassigned), collapsing the visible fleet 38→7 while machines were provably on.
- **Change:** `pgb_watch` probe inside fleetwatch — recognizes the wedge signature (or a dead admin console), two strikes → automatic restart, 10-minute cooldown.
- **Effect:** the wedge can no longer take the fleet down for longer than ~2 minutes, unattended.

### 2.4 Postgres zombie-transaction guard
- **Problem:** worker restarts through the old network left idle-in-transaction sessions holding row locks for up to 21 hours — they blocked migrations and job updates.
- **Change:** `idle_in_transaction_session_timeout = 10min` (ALTER SYSTEM); one pre-existing zombie killed manually (the timeout does not re-arm for sessions already idle when set).
- **Effect:** lock-blocking incidents ended.

---

## 3. Network (the single biggest speed change)

### 3.1 The problem
The head Mac's only link was a Windows PC's 2.4 GHz Wi-Fi hotspot. **Every** database query, heartbeat, and PDF download from ~40 machines crossed that one saturated channel (RTT 1.6–2.2 s under load; cold DB connects 9–16 s). Under wave load, bulk traffic starved heartbeats → hosts "went offline and online" in the dashboard while actually working. This was the root cause of every roster-drop incident.

### 3.2 The change
- Ethernet cable into the head; the head made **Ethernet-only** (Wi-Fi off) at a **static** LAN address `192.168.1.250` (static because on the first night as DHCP, the router re-leased the head's address to a worker PC and stranded the entire fleet for a day).
- Every host's `DATABASE_URL` **and** `FLEET_HEAD_URL` (the PDF download endpoint) repointed to the LAN address — rolled with a self-verifying per-host script (each host reports counts of replaced/remaining addresses).
- Dashboard: `http://192.168.1.250:8000`.

### 3.3 The effect (measured)
- Connection setup: **9–16 s → 14 ms** (~1000×).
- Statement latency: seconds → sub-millisecond.
- The "hosts dropping during generation" phenomenon ended completely: the record runs showed a rock-steady roster at 34–35 hosts for their entire duration.

---

## 4. Configuration integrity (the reverter class)

### 4.1 The problem
Fleet config kept silently reverting: hosts reappeared with the old database address, direct `:5432` connections, 2+2 pools, and flattened slot counts after every restart. Cause: each host's supervisor re-seeds `.env` from a local template on start when a hardcoded validity gate fails — and both the gate and the templates carried stale values. Separately, the provisioning seed handed new hosts the oldest config of all.
### 4.2 The change
- Supervisor gate updated to the current head address; **every on-host seed template replaced** with the corrected config (LAN address, pgbouncer port, pool 6+4, current API cap);
- head-side provisioning seed (`worker.env`) fixed identically, so future auto-provisioned hosts are born correct;
- rolled with uploaded script files, not inline commands (inline remote edits proved to silently no-op).
### 4.3 The effect
Restarts now *converge* hosts to the correct configuration instead of un-fixing them. This closed the recurring mystery of "fixed hosts coming back wrong."

---

## 5. Capacity configuration

### 5.1 Slot tiers (concurrent lessons per host)
- RAM-decided, self-measured by each machine at tune time: **≥12 GB → 8 slots, below → 4 slots** (8 GB classroom PCs idle with only ~0.5–1.5 GB free; 8 was field-proven on 16 GB machines).
- Current fleet: **11 hosts × 8 + 24 hosts × 4 = 184 slots** across 35 serving hosts.

### 5.2 The API-call ceiling (the final 6× jump)
- **Problem:** the per-credential limiter allowed 32 concurrent model calls per key — correct when the fleet ran many per-host keys, but a reseed had collapsed everyone onto **one** shared key: ~350 wanted calls fought for 32 lanes; half of all calls spent 120 s in the wait queue and retried (measured: 1,209 calls/10 min, 46 % slot-wait failures, median call 120.0 s = the wait timeout).
- **Change:** `CREDENTIAL_MAX_CONCURRENT_GEMINI` **32 → 200** fleet-wide (the operator's key has high provider limits; the governor was ours, not Google's).
- **Effect (measured within 5 minutes):** calls/min 121 → **508** (8.5/s); call failure rate 46 % → 1.5 %; average call 104 s → 30–43 s; completed lessons **~1/min → 8–9/min and still accelerating** when stopped.

### 5.3 Fleet hygiene supporting speed
- **Version floor 1073** — old-code hosts cannot claim, so the last QueuePool-capable machines can never pollute a run.
- **Four problem hosts (Host-02/-05/-44/-52) permanently off-boarded** on operator's order: worker task disabled at first contact, excluded from auto-provision and Wake-on-LAN, fenced by the floor. Three other former stragglers (Host-06/-40/-47) were converted to current code and serve normally.
- **Windows idle-sleep fixed fleet-wide** (`powercfg`, Wake-on-LAN preserved) — machines stopped vanishing after 15 idle minutes; fleet grew 15 → 40+ live.
- **Automation:** `fleetwatch` auto-provisions returning machines every 60 s and wakes powered-off ones every 20 min; the monitoring display window was widened (90 s → 180 s) to stop cosmetic "offline" flapping.

---

## 6. Measured record progression

| Date | Peak concurrent | Finish rate | Failures |
|---|---|---|---|
| Before (baseline) | 4 (one host) | ~0.2/min | frequent |
| Aug 14 — first max-out (260 lessons) | 80 on 19 hosts | ~3.2/min | 5 % + cascade |
| Aug 17 — after deploy + pgbouncer + fd fix (Ethernet day) | 138 on 34 hosts | ramp cut short | **0** |
| Aug 19 — after cap 32→200 | 140+ on 35 hosts | **8–9/min** (23 lessons in the final 3 min) | **0** real (1 bookkeeping artifact) |

**Failure classes eliminated** (each present in the Aug 14 taxonomy, zero in the final runs): QueuePool exhaustion, empty-error deaths, connection-lost, fetch deaths, heartbeat starvation/roster flap, reclaim attempt-burning, thundering-herd collapse, pgbouncer wedge (auto-healed), fd refusals, config reversion.

**Validation of output** (Aug 19): 39/39 completed lessons had all 12 parts present (avg 4,271 chars/part, none thin), 420 parts judge-passed, real spend receipts (1,874 model calls, 22.8 M tokens ≈ 585 k/lesson) — genuine homework, not bookkeeping.

---

## 7. Current standing capacity and the next levers

- **Standing:** 35 hosts / 184 slots / 200 concurrent API calls / all traffic on Ethernet / all watchdogs armed.
- **Estimated full speed at current config:** ~13–15 finished homeworks/minute (call-lane bound: ~12,000 call-seconds available per minute ÷ ~850 call-seconds per lesson) ≈ **800–900/hour**. Not yet field-confirmed for a full 30-minute saturated window — the runs were stopped by the operator mid-ramp.
- **Next levers, in order:** raise the call cap toward the per-host fan-out ceiling (~270) → ~20/min (slot-bound); more/bigger machines (a dedicated 32 GB PC fits 100+ slots by RAM; the tested-safe start is 24–32 with matching call caps); multiple API credentials to spread the per-key load.
- **Fixed and non-negotiable:** per-lesson duration (~9 min median) is determined by the 12-part dependency graph — concurrency is the only throughput lever.
