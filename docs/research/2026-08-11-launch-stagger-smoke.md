# Acceptance smoke — batch-launch wave stagger (2026-08-11)

Plan: `docs/superpowers/plans/shipped/2026-08-11-batch-launch-stagger.md` (Task 8).
Harness: `scripts/smoke_launch_stagger.py`, run as
`uv run python -m scripts.smoke_launch_stagger`.

**Verdict: PASS.** Both halves passed. **Total spend $1.1220** across 27 real
model calls, 0 failures — within 3% of the plan's $1.15 estimate.

## Environment

| | |
|---|---|
| Database | `edu_scratch_stagger` on `127.0.0.1:5432`, migrated to head `0053` |
| Production | **never written**; read once, read-only, to copy one book's metadata |
| Book | `481be5d8…` history g8 ru, real 18.6 MB PDF on disk via `VAR_DIR` |
| Knobs | `BATCH_LAUNCH_WAVE_SIZE=2`, `BATCH_LAUNCH_WAVE_INTERVAL_SECONDS=20` (20s, not the shipped 60s, purely so the wait is bearable) |
| Transport | `api`; content `gemini-3.6-flash`, judge `gemini-3.5-flash`, solver `gemini-3.1-pro-preview`, extract `gemini-3.5-flash-lite` |

## Part (a) — schedule proof + claim-through ($0, zero model calls)

Six lessons launched through the **real** `launch_batch`:

```
lesson#0  due_in=  0s      lesson#3  due_in= 20s
lesson#1  due_in=  0s      lesson#4  due_in= 40s
lesson#2  due_in= 20s      lesson#5  due_in= 40s
```

- **PASS(a1)** — three waves at ~[0, 20, 40]s, two jobs each, **in TOC order**.
  Response: `{"wave_size": 2, "interval_seconds": 20, "jobs_launched": 6,
  "waves": 3, "last_start_offset_seconds": 40}`.
- **PASS(a2)** — `queue_depth()` measured **2 → 4 → 6** as each wave fell due.
  This is the step that makes the gate self-contained: a future-stamped row was
  observed *becoming* claimable on real Postgres, rather than the claim gate
  being taken on trust from pre-existing tests.

## Part (b) — generation on a job the stagger actually held back ($1.1220)

Deliberately **not** a fresh single-lesson launch: a 1-lesson launch is always
wave 0, offset 0, so `scheduled_at` is never stamped and it would prove nothing
about this feature. Instead every other job was cancelled so the *only*
claimable row was a **wave-2** job (stamped `+40s`), which was then claimed and
run through the real pipeline.

- Claimed id **asserted equal** to the held-back id — so the row generated is
  provably the one the stagger pushed into the future.
- Result: **`status=done`, 12 done phases, 27 calls, 0 failures.**

| Model | Cost |
|---|---|
| `gemini-3.5-flash` (judge) | $0.5352 |
| `gemini-3.6-flash` (content) | $0.4252 |
| `gemini-3.1-pro-preview` (solver) | $0.1322 |
| `gemini-3.5-flash-lite` (extract) | $0.0293 |
| **Total** | **$1.1220** |

## Three findings the run produced that were not planned

Earlier iterations left rows behind, which pushed the launcher down its *other*
branches — each behaved correctly, so these are free confirmations:

1. **In-launch resume staggers identically.** With the prior run's rows left
   `cancelled`, the launch reported `jobs_created=0, jobs_resumed=6` and the
   identical `waves: 3, last_start_offset_seconds: 40`. The resume branch feeds
   the same shared counter, on real data.
2. **Adopt/skip consumes no wave slot.** With rows left `pending`, the launch
   reported `jobs_skipped=6` and `stagger: {jobs_launched: 0, waves: 1}` — the
   load-bearing rule, observed rather than argued.
3. **A vacuous assertion in this harness, caught and fixed.** The first version
   logged `PASS(b)` on `status=failed, done_phases=0`. It now exits non-zero
   unless `status == "done"` **and** `done_phases > 0`. Recorded because the
   defect was the exact class this project's review discipline exists to catch,
   and it was written by the controller, not a subagent.

## Reproducing

```bash
export DATABASE_URL='postgresql+asyncpg://edu:PW@127.0.0.1:5432/edu_scratch_stagger'
export PROD_DSN_READONLY='postgresql://edu:PW@127.0.0.1:5432/edu_copy'   # READ-ONLY
export VAR_DIR=/Users/macmini5/Documents/Homework-Content-Generation-Automation/var
export SMOKE_GENERATE=1          # omit to run part (a) only, at $0
uv run python -m scripts.smoke_launch_stagger
```

The harness refuses to start unless `DATABASE_URL` is explicit, pins
`127.0.0.1`, and does **not** name `edu_copy`; it also asserts `app.config`
resolves inside this worktree, because a git worktree has no `.env` of its own
and `load_dotenv` otherwise walks up to `~/Documents/.env` and aims at a remote
host.
