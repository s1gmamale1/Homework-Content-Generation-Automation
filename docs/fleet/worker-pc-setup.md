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

---

## Part B — Set up each worker PC (repeat on every PC)

Do this once per PC. After that, the PC generates on its own forever.

1. **Install Docker**, and set it to **start on login** (Docker Desktop →
   Settings → "Start Docker Desktop when you log in"). This is what makes the
   worker come back automatically after every reboot.

2. **Copy one file** onto the PC: `docker-compose.worker.yml`.

3. **Point the worker at the head for textbooks.** Set `FLEET_HEAD_URL` to the
   head's API (e.g. `http://<HEAD_IP>:8000`) plus a matching `AUTH_TOKEN` — a
   worker that's missing a book's PDF now **fetches it from the head on demand**
   and caches it locally (ROADMAP R13, shipped). No manual copying needed.
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
| api-claude work | `ANTHROPIC_API_KEY` in the worker env/.env |
| api-gemini work | `GEMINI_API_KEY` **or** a Vertex service account (`GOOGLE_APPLICATION_CREDENTIALS` + `GOOGLE_CLOUD_PROJECT`) — **the SA path must be THIS machine's path**, not copied from another OS |

A worker missing a credential simply **never claims jobs that need it** (it
logs which side is missing at startup) — cli jobs are unaffected.

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
- **AI login:** each PC signs in to the CLIs once, per subscription account. For API billing, keys/SA files are per-PC too (see "Choosing who pays").
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
