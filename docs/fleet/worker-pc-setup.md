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
2. **Open the firewall** for the database port (**5436**): allow inbound TCP 5436
   in Windows Defender Firewall.

That's it — the head is ready. It just needs to stay on.

---

## Part B — Set up each worker PC (repeat on every PC)

Do this once per PC. After that, the PC generates on its own forever.

1. **Install Docker**, and set it to **start on login** (Docker Desktop →
   Settings → "Start Docker Desktop when you log in"). This is what makes the
   worker come back automatically after every reboot.

2. **Copy one file** onto the PC: `docker-compose.worker.yml`.

3. **Make the textbooks reachable.** The worker reads each book's PDF from a
   local folder. Easiest for now: put the `var/books` folder on the PC (a shared
   network drive mapped to that path, or a straight copy). *(Auto-syncing this is
   planned — see "Rough edges" below.)*

4. **Log in to the AI tools once.** In a terminal, run `claude` and `gemini` and
   complete the sign-in. (This uses your subscription accounts.)

5. **Start the worker** — one command:

   ```
   set DATABASE_URL=postgresql+asyncpg://edu:edu@<HEAD_IP>:5436/edu_copy
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

## Rough edges (still manual today — being honest)

- **PDFs:** each worker needs the textbook files on its own disk. A shared folder
  works now; automatic delivery to each PC is planned (roadmap item **R13**).
- **AI login:** each PC signs in to the CLIs once, per subscription account.
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
