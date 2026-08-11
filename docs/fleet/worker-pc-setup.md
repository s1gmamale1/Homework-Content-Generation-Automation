# Getting PCs to generate homework — plain-English runbook

How to turn any PC into a "worker" that joins the fleet and generates homework.

## The idea (in one picture)

- **One PC is the "head."** It runs the shared database (the to-do list) and the
  dashboard. It coordinates the work; it does **not** generate.
- **Every other PC is a "worker."** It watches the head's to-do list, grabs
  lessons one at a time, and generates them. Add as many workers as you like —
  they split the work automatically and can never grab the same lesson twice.

```
        ┌─────────────┐
        │   HEAD PC   │   database (to-do list) + dashboard
        └──────┬──────┘
   ┌───────────┼───────────┐
┌──┴──┐     ┌──┴──┐     ┌──┴──┐
│ PC1 │     │ PC2 │     │ PC3 │   workers — each grabs lessons & generates
└─────┘     └─────┘     └─────┘
```

---

## Part A — Set up the head (do once)

This PC is already your head. To let the other PCs reach it:

1. **Find this PC's network address.** Open a terminal, run `ipconfig`, and note
   the **IPv4 address** (looks like `192.168.x.x`).
2. **Open the firewall** for the database port (**5432**): allow inbound TCP 5432
   in Windows Defender Firewall.

That's it — the head is ready. It just needs to stay on.

> **Run the head with `WORKER_CONCURRENCY=0`** so the API/head process does **not** also
> claim and generate jobs — the head coordinates, the worker PCs generate. (Worker PCs run
> with `WORKER_CONCURRENCY>0`.) Set it in the head's `.env`.

---

## Part B — Set up each worker PC (repeat on every PC)

**Automated path (recommended):** run **`SETUP-ALL.bat`** on the worker PC. It does the
whole bring-up for you — pins the repo to branch **`Nggaev-v2`**, boots the worker
**keyless** (keys arrive later via Fleet → Keys), and sets `VAR_DIR` for the local PDF/key
cache. The manual steps below are what that script does under the hood; run them by hand
only if you're not using `SETUP-ALL.bat`. (`SETUP-ALL.bat` lives in a separate setup repo.)

Do this once per PC. After that, the PC generates on its own forever.

1. **Install Docker**, and set it to **start on login** (Docker Desktop →
   Settings → "Start Docker Desktop when you log in"). This is what makes the
   worker come back automatically after every reboot.

2. **Copy one file** onto the PC: `docker-compose.worker.yml`.

3. **Point the worker at the head for textbooks.** Set `FLEET_HEAD_URL` to the
   head's API (e.g. `http://<HEAD_IP>:8000`) plus the same strong `AUTH_TOKEN`
   used by the head and `ALLOW_INSECURE_LOCAL_AUTH=false` — a
   worker that's missing a book's PDF now **fetches it from the head on demand**
   and caches it locally (ROADMAP R13, shipped). No manual copying needed.
   Empty, weak, historical `123`, or mixed weak+strong token configuration now
   refuses standalone-worker startup. Never enable anonymous local mode on a
   fleet worker.
   *(Alternative: mount a shared `var/books` folder on the PC and skip
   `FLEET_HEAD_URL` — the worker reads the PDF straight off disk.)*

4. **Log in to the AI tools once.** In a terminal, run `claude` and `gemini` and
   complete the sign-in. (This uses your subscription accounts.)

5. **Start the worker** — one command:

   ```
   set DATABASE_URL=postgresql+asyncpg://edu:edu@<HEAD_IP>:5432/edu_copy
   docker compose -f docker-compose.worker.yml up -d
   ```

   Replace `<HEAD_IP>` with the head's address from Part A. (Swap `edu_copy` for
   your real database name when you go live; match the port to the head's DB.)

From now on this worker **starts whenever the PC is on**, restarts itself if it
crashes, and shows up on the dashboard automatically.

---

## Part C — Generate

1. On the **head**, open the dashboard → **Fleet**.
2. Pick a subject and click **Launch.** This drops one "ticket" per lesson onto
   the shared to-do list.
3. Every worker PC grabs lessons and generates them. Watch the funnel fill:
   **pending → running → done.**

More PCs = more lessons generated at the same time = faster.

---

## Part D — Check it's working

- Within a few seconds the PC appears as a **green worker card** on the dashboard.
- Lessons move to **done**. Expand a batch to watch individual lessons live.
- To prove no two PCs collide, run `scripts/fleet_contention_smoke.py` (or just
  watch — each lesson is only ever claimed once, guaranteed by the database).

---

## Choosing who pays (Phase 4/4.1 — the billing toggle)

Every job has a **transport**: `CLI` (default — the logged-in CLI subscriptions,
$0 marginal, rate-capped) or `API` (pay-per-token keys, uncapped). Since Phase
4.1 you can also override the billing **per role** on each job: the **Extract**
and **Judge** selects on the launch forms take `Auto (follow job) / CLI / API`.
Example split (the one this fleet actually runs): gemini-API content billed to a
Vertex credit + CLI extract on a Gemini subscription + CLI judge on the Claude
subscription.

**What a worker PC needs depends on what you want it to claim:**

| Worker should claim… | Needs on that PC |
|---|---|
| CLI-only jobs | just the CLI logins (step 4 above) — no keys |
| api-gemini work | a Vertex service account, applied live via **Fleet → Keys** (assign by hostname — see "SA key distribution" below); this is the **primary** path |
| api-claude work | (legacy/fallback) `ANTHROPIC_API_KEY` in the worker env/.env |

**Primary key path is Fleet → Keys** (assign a key to the worker's hostname; the worker
boots keyless and picks it up live — see "SA key distribution" below). The manual `.env`
env vars — `GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT` (Vertex, **the path
must be THIS machine's path**) and `ANTHROPIC_API_KEY` (claude) — still work as a
**legacy/fallback** option, but prefer the Keys UI for a fleet.

A worker missing a credential simply **never claims jobs that need it** (it
logs which side is missing at startup) — cli jobs are unaffected.

**SA key distribution (Fleet → Keys panel):** instead of copying `.json` files manually, upload each service-account key once from the web UI (Fleet → Keys). The head stores the key and you assign it to a worker hostname from the same panel. The worker then downloads and applies the key automatically on the next startup or main-loop tick — no manual `.env` edit and no restart needed. Applying the key writes `<var_dir>/sa_keys/active.json`, updates `GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CLOUD_PROJECT` in the process environment and the worker's `.env`, and recomputes the capability flags so a previously keyless idle worker starts claiming gemini-api jobs immediately. The complete SA-key API is header-only; `?token=` is rejected on list/upload/download/assignment/scrub alike, and the vault stays closed in anonymous local mode. Credential-file reads/writes now use the private atomic vault service rather than direct path I/O.

**Operator-token rotation is a coordinated hard cut.** Follow
[`docs/runbooks/operator-token-rotation.md`](../runbooks/operator-token-rotation.md):
preserve any foreign API pause, then install a temporary version floor above
the target code because the API pause does not block CLI claims. Drain and stop
every worker process (terminal DB rows alone do not prove post-done Notion work
ended), require zero limiter slots, and stage one strong token everywhere with
`WORKER_CONCURRENCY=0` on the head. The operator restarts the head; workers then
restart behind the unchanged floor and publish the exact expected
`auth_token_fingerprint`. Attest every online model-calling process—two
processes on one PC count twice—by code SHA/version, fingerprint, concurrency,
capabilities, and heartbeat. Offline stragglers remain
version-fenced and, where applicable, tombstoned until updated; powered off is
not rollout-complete.

Token rotation does not change credential ownership. Preserve assignment rows,
all six stored Vertex objects, Host-59's existing assignment/scrub state, every
plain `GEMINI_API_KEY`, and active Vertex files byte-for-byte. Automation may
prepare worker files/restarts under the global pause, but it must not kill or
restart the user-owned head process.

**Two required one-time settings in `~/.gemini/settings.json` on every worker:**

```json
{ "security": { "auth": {} }, "advanced": { "ignoreLocalEnv": true } }
```

- **No `selectedType`** — a persisted auth choice (re-created by any interactive
  `gemini` run) overrides the per-job billing toggle. The worker warns at
  startup if present.
- **`ignoreLocalEnv: true`** — otherwise gemini-cli imports `GOOGLE_CLOUD_PROJECT`
  / `GEMINI_API_KEY` from the repo's `.env` *inside every spawn*, bypassing the
  app's auth shaping (this 403'd a real batch on 2026-06-12). Needs gemini-cli
  ≥ 0.46 — **pin the same CLI versions on all PCs**.
- Windows note: write the file WITHOUT a BOM (PowerShell 5.1's `Out-File
  -Encoding utf8` adds one and gemini-cli rejects the file).

Full billing/acceptance detail: `docs/runbooks/phase4-transport-operator-acceptance.md`.

---

## Rough edges (still manual today — being honest)

- **PDFs:** ✅ solved (ROADMAP **R13**, shipped). A worker missing a book's PDF
  fetches it from the head on demand (`FLEET_HEAD_URL` + matching `AUTH_TOKEN`)
  and caches it locally; a shared `var/books` folder still works as an alternative.
- **SA keys for Vertex/gemini-api:** ✅ solved (worklog 0106, shipped). Upload keys + assign
  to hostnames from Fleet → Keys; the worker pulls and applies them live on startup/each loop
  tick (see "SA key distribution" in "Choosing who pays" above) — no manual file copy or restart.
- **AI login:** each PC signs in to the CLIs once, per subscription account. For API billing,
  keys are now distributed via Fleet → Keys (see above).
- **No "Start" button yet:** you bring a worker online by provisioning the PC
  (Part B). A dashboard Start/Pause/Off button is a future feature
  (`fleet-ctrl-3/4`). For now, the Docker "start on login" setting is your
  auto-start.

---

## Quick reference

| You want to… | Do this |
|---|---|
| Add a worker PC | Part B (once per PC) |
| Start generating | Dashboard → Fleet → Launch a subject |
| Add more speed | Provision more PCs (Part B) — they auto-join |
| Stop a worker PC | `docker compose -f docker-compose.worker.yml down` on that PC |
| See who's online | Dashboard → Fleet → worker cards |
