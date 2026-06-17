# Windows Fleet Worker — Setup Instructions (R13 pull-on-demand PDF test)

**You are setting up a Windows PC as a "worker" that joins a homework-generation fleet.**
A "head" Mac is already running: it holds the shared database (the job list) and serves
the textbook PDFs. This Windows PC will claim a lesson, **fetch the textbook PDF it doesn't
have from the head over the network** (this is the feature under test, "R13"), and generate
the homework.

You (the AI agent reading this) should execute the steps below on this Windows machine,
verify each one's expected output before moving on, and stop + report if any check fails.

---

## The fixed facts (already true — do not change)

| Thing | Value |
|---|---|
| Head Mac IP | `192.168.1.74` |
| Head database (Postgres) | `192.168.1.74:5432`, db `edu_copy`, user `edu`, password `edu` |
| Head API (serves PDFs) | `http://192.168.1.74:8000` |
| Shared auth token | `123` |
| Repo | `https://github.com/s1gmamale1/Homework-Content-Generation-Automation.git` |
| Branch to use | `feat/r13-pull-on-demand-pdf` |
| Test book id (Kimyo, ~3.8 MB) | `54cd9ff3-7aed-484c-bba6-7163ad46fda9` |

This PC and the head must be on the **same Wi-Fi/LAN** (this PC's IP should start `192.168.1.`).
Check with `ipconfig` — if your IPv4 is on a different subnet, fix the network before continuing.

---

## Prerequisites to install (once)

1. **git** — https://git-scm.com/download/win
2. **uv** (Python toolchain) — in PowerShell:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
3. **claude CLI** (Claude Code) — needed to actually generate. Install it, then run `claude`
   once and **log in** to your subscription. (Logins do NOT transfer between machines — this
   PC must sign in on its own.)

You do NOT need Node/npm, Docker, or any other provider CLI for this test — generation runs
entirely on `claude`.

---

## Step 1 — Get the code

```powershell
git clone https://github.com/s1gmamale1/Homework-Content-Generation-Automation.git
cd Homework-Content-Generation-Automation
git checkout feat/r13-pull-on-demand-pdf
uv sync
```

**Expected:** `git checkout` reports it's on `feat/r13-pull-on-demand-pdf`; `uv sync`
finishes creating the virtual env with no errors.

**Verify you have the R13 code** (the worker-side helper must exist):
```powershell
Test-Path app\services\book_fetch.py    # must print: True
```
If this prints `False`, you are on the wrong branch — re-run `git checkout feat/r13-pull-on-demand-pdf`.

---

## Step 2 — Confirm the head is reachable (do this BEFORE configuring)

Two separate connections must work — the database and the API.

```powershell
# (a) Database port reachable?
Test-NetConnection 192.168.1.74 -Port 5432
#   -> TcpTestSucceeded : True

# (b) API up + the PDF endpoint works with the token?
curl.exe "http://192.168.1.74:8000/health"
#   -> {"status":"ok"}  (or HTTP 200)

curl.exe -o probe.pdf "http://192.168.1.74:8000/api/v1/books/54cd9ff3-7aed-484c-bba6-7163ad46fda9/source.pdf?token=123"
#   -> downloads a ~3.8 MB probe.pdf
Remove-Item probe.pdf    # delete it — that was only a connectivity probe
```

**If (a) fails:** the head's database isn't reachable — likely the Mac firewall or the PCs
are on different networks. STOP and report this; it must be fixed on the head side.
**If (b) fails:** the head API isn't reachable or the token is wrong. STOP and report.

> Note: this PC, as a worker, only makes **outbound** connections to the head, so you should
> not need to change the Windows firewall. Only stop if the checks above actually fail.

---

## Step 3 — Create the worker's `.env`

Create a file named `.env` in the repo root with EXACTLY this content:

```dotenv
# Point at the head Mac's shared database (the job list)
DATABASE_URL=postgresql+asyncpg://edu:edu@192.168.1.74:5432/edu_copy

# R13: where to fetch a missing textbook PDF from (the head's API)
FLEET_HEAD_URL=http://192.168.1.74:8000

# Must match the head so the PDF fetch is authorized
AUTH_TOKEN=123

# Turn this machine into an active worker (claims & generates)
WORKER_CONCURRENCY=2

# Generation providers — all claude for this test (extract + content + judge)
EXTRACT_PROVIDER=claude
EXTRACT_MODEL=claude-sonnet-4-6
JUDGE_PROVIDER=claude
JUDGE_MODEL=claude-opus-4-7

# A full claude job can take >10 min; keep the generous timeout
JOB_TIMEOUT_SECONDS=1800
```

**Do NOT** put a `VAR_DIR` line — leaving it default means PDFs land at
`var\books\<book_id>\source.pdf`, and a fresh clone has none yet. **That is the point of the
test:** this PC starts with no PDF and must fetch it.

**Verify the test pre-condition** (the PDF must be ABSENT before we start):
```powershell
Test-Path var\books\54cd9ff3-7aed-484c-bba6-7163ad46fda9\source.pdf   # must print: False
```

---

## Step 4 — Start the worker

Run the worker-only entrypoint (NOT `uvicorn main:app` — that one runs a full API and would
reset other machines' running jobs; the worker module is the correct standalone entry):

```powershell
uv run python -m app.services.worker
```

**Expected log lines within a few seconds:**
- `worker <THIS-PC-NAME>:<pid> starting | concurrency=2 ...`
- It should NOT error about the database — if it can't connect, it will say so loudly.

Leave this running in the foreground so you can watch it. (If you run it in the background,
keep tailing its output.)

**Verify the head sees this worker:** it registers a heartbeat in the shared DB. On the head's
dashboard (Mac) it appears as a green worker card. You can also confirm from here:
```powershell
curl.exe "http://192.168.1.74:8000/api/v1/workers?token=123"
#   -> JSON listing this PC with "online": true
```

---

## Step 5 — Trigger a job (the operator does this ON THE MAC)

Tell the human operator:
> "On the Mac, open `http://localhost:8000` → **Fleet** → launch the **Kimyo** book with
> **provider = claude**, just 1–2 lessons."

(The head's own worker is intentionally OFF, so this Windows PC is the only machine that can
pick up the lesson.)

Once launched, the job lands on the shared to-do list and this worker will claim it within a
few seconds. Watch the worker log from Step 4.

---

## Step 6 — Confirm R13 worked ✅ (the whole point)

As the worker processes the lesson, verify these in order:

1. **The PDF gets fetched onto THIS machine** — the file that was absent in Step 3 now exists:
   ```powershell
   Test-Path var\books\54cd9ff3-7aed-484c-bba6-7163ad46fda9\source.pdf   # now: True
   ```
   **This appearing is the R13 proof** — the worker had no PDF, fetched it from the head, and cached it.

2. **No "Book PDF missing on disk" error** in the worker log. Instead the `extract` phase runs.

3. **The lesson finishes** — it progresses through the phases to `done`. On the Mac dashboard
   the lesson moves pending → running → **done**, attributed to this Windows worker.

If all three hold, the cross-machine fleet + pull-on-demand PDF delivery is verified end-to-end.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Worker can't connect to DB at startup | `Test-NetConnection 192.168.1.74 -Port 5432` fails → Mac firewall or wrong subnet. Fix on head. |
| Log says `Book PDF missing on disk` (no fetch attempt) | `FLEET_HEAD_URL` missing/empty in `.env`. It must be `http://192.168.1.74:8000`. |
| Fetch fails with `head returned HTTP 401` | `AUTH_TOKEN` in `.env` ≠ `123`. |
| Fetch fails with `head returned HTTP 404` | Wrong book id, or that book's PDF isn't on the head. |
| Generation starts then fails at the model step | `claude` CLI not installed or not logged in on this PC. Run `claude` and sign in. |
| Worker never claims the job | It's not running, or the operator hasn't launched yet, or the head's worker grabbed it (head must be `WORKER_CONCURRENCY=0`). |

## Cleanup (after the test)

- Stop the worker with `Ctrl+C`.
- The fetched PDF under `var\books\...` can stay (it's a valid cache) or be deleted.
