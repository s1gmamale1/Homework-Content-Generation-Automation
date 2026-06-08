# Autonomous Generation Fleet — Design Spec

> **Status:** Approved for `writing-plans`, rev 5 (round-3 fixes: real pipeline shape `extract → content phases`, `owner_pc` removed, `generation_mode`→`provider` mapping, NETS-unverified caveat; rounds 1–3 verified). Brainstormed 2026-06-06.
> **Sibling reference:** NETS / Creative-Content-Automation (`dhost` @ `205cf6f`). We port its *automation logic*, not its full infrastructure (§1, §8).
> **⚠ NETS claims are unverified from THIS repo.** Everything attributed to NETS (credential-pool schema, commit `205cf6f`, "doesn't run Swarm", `head-address.txt`, "skipped encryption-at-rest", the 6 worker types) describes an **external repo not accessible from this codebase** — treat as **verify-at-port-time, not ground truth**, especially at **Phase 4b** (credential-pool ported verbatim).
> **Companion:** `docs/PRODUCTION_AUTONOMOUS_GENERATION.md` — informs §1a (judge always Opus; dedicated fleet Claude accounts).

**Goal:** Let an operator point the system at a scope (a Notion subject → its lessons) and have a fleet of ~10 PCs generate every lesson's homework packet on its own — pulling work from **one shared database**, healing itself when a PC dies, and surfacing only finished packets and failures for review.

**Architecture:** One central Postgres "head" is the entire control plane (queue + state + fleet registry + batch rollups + cost ledger). N stateless Dockerized workers — one per PC — poll that one DB, claim a whole job (one lesson), run our existing phase-DAG pipeline, and advance it. A dashboard launches batches and controls the fleet. No broker, no scheduler, no Docker Swarm.

**Tech:** Existing FastAPI + React + Postgres + phase-DAG pipeline + job-resilience (worklog 0031). Adds: a Docker worker image, a `batches` aggregate, a `workers` registry, a fleet dashboard, a fallible Notion→TOC lesson-resolution step, and (final phase) a Gemini/Google-Cloud-API generation path with a credential pool + cost ledger.

---

## 1. Design principle: adopt the automation, cut the complexity

Adopt NETS's automation logic while exploiting the fact that our work is *easy* relative to theirs: every phase output is **markdown in the DB**, not large media on a PC's local disk. That single difference lets us delete NETS's three most complex subsystems.

| We ADOPT (NETS's automation logic) | We CUT (NETS complexity we don't need) |
|---|---|
| One central Postgres "head"; all PCs share it | **Docker Swarm** — even NETS doesn't really run it; we use plain per-PC Docker |
| DB-as-queue, atomic claim (`FOR UPDATE SKIP LOCKED`) + lease | **Host-affinity** (`owner_pc`/`assigned_pc`/cross-host stealing) — no local artifacts to pin |
| Batch launcher: Notion subject → N jobs | **Per-stage workers** (6 worker types) — we run a whole job's DAG in one worker |
| `workers` table + heartbeat = fleet liveness | **PgBouncer** (deferred until connection counts demand it; §9) |
| Batch aggregate rollup (progress + spend) | **Autoscaler** (fixed worker count per PC initially) |
| Cost ledger + budget cap + kill-switch *(money phase only)* | |
| Credential pool for Gemini quota *(money phase only)* | |

**Unit of distribution = the whole JOB (one lesson), not the phase.** A PC claims a job and runs the existing pipeline — **`extract → content phases (DAG-parallel)`** — in-process exactly as today (verified `pipeline.py:100,135,216`; the old `classify` and `assembly` stages were **removed** — `flows.py:1` "MVP — no classify"). If it dies, any other PC's existing sweep re-claims the whole job. Pure work-stealing, zero affinity.

**What we already have (verified — do NOT rebuild):**
- `claimed_by` + `claimed_at` on `homework_jobs` (`homework_job.py:50-51`); implicit lease via `reclaim_stale_seconds=120` (`config.py:60`); claim refresh via `touch_claim`.
- **Continuous self-healing reclaim** in every worker (`worker.py:98-104`): `_sweep_stuck_jobs` on startup + every `sweep_interval`; **any** live worker reclaims **all** stale `running` jobs → `pending` (`reclaim_stuck_jobs`). There is **no new reclaim engine to build.**
- Standalone worker entrypoint (`run_standalone`/`__main__`, `worker.py:341/366`); `worker_concurrency=0` disables the embedded worker (`main.py`).
- DB-as-queue with `FOR UPDATE SKIP LOCKED`, the full phase-DAG pipeline, `phase_outputs` + `create_or_reset` resume, retry/attempts bookkeeping.

So the automation core is mostly *wiring existing parts*. The genuinely-new surface is: the `workers` table, the `batches` aggregate, the Notion→TOC lesson resolver, the dashboard, and the entire money layer (Phase 4).

---

## 1a. Generation accounts, the CLI-vs-API choice, and the judge

**Lockout is NOT a concern.** The operator runs **dedicated Claude Max accounts for the fleet, separate from their personal Claude Code** — so, unlike the worry in `docs/PRODUCTION_AUTONOMOUS_GENERATION.md`, the fleet may freely use `claude`.

**Per-batch generation mode — operator chooses at launch (`generation_mode = cli | api`):**
- **CLI mode (now, pre-GCP-credit):** generation runs **`claude`-first** on **3 dedicated Claude $200 Max accounts distributed across the 10 PCs**. When an account hits its 5h/weekly session limit, the **existing failover chain** continues the job on any available provider (`codex` / `gemini`-CLI / `kimi` / `opencode`) — already-shipped behaviour, no new pinning logic.
- **API mode (once GCP credit lands; testable now with existing credits):** generation runs on the **Google Cloud / Gemini API** (Phase 4: credential pool + cost ledger).
- Both modes coexist; the choice is a **field on the batch**, surfaced in the launch UX.

**Judge: always Claude Opus** (`judge_model="claude-opus-4-7"`, `config.py:82-83`) in **both** modes — the quality gate stays strong even where human review is absent. It's ~1 call per phase (low volume vs generation), drawn from the fleet claude accounts; even API-mode batches judge on Opus. (The earlier "pin the judge off claude" tension is **resolved** by the dedicated accounts — we keep Opus, no `JUDGE_PROVIDER` override.)

**Account distribution (3 accounts ↔ 10 PCs)** is operator setup: spread the 10 PCs across the 3 accounts (~3–4 PCs each); PCs sharing an account share its caps and fail over when it's exhausted. Which account a PC uses is config the operator sets.

**Budget-cap meaning depends on mode:** in **CLI mode** there are no real per-call dollars (flat-plan accounts; `kimi` reports 0 tokens), so the cap is a **job-count / token-estimate guardrail** — labelled as such. In **API mode** the cap is a **real-dollar cost ledger + kill-switch** (Phase 4). Don't render a dollar guarantee in CLI mode.

---

## 2. Topology

```
            ┌──────────────────────── HEAD (one PC / server) ────────────────────────┐
            │  Postgres (the ONLY shared state):                                       │
            │    homework_jobs (queue+state)  ·  batches (rollup)  ·  workers (fleet)   │
            │    phase_outputs  ·  agent_usages→cost_events  ·  credential pool         │
            │  FastAPI: existing API + review console + NEW fleet dashboard             │
            └───────────────────────────────▲──────────────────────────────────────────┘
                                             │  one DB_URL (discovery file / env)
        ┌────────────────────┬───────────────┼───────────────┬────────────────────┐
   PC#1 worker(s)       PC#2 worker(s)   PC#3 worker(s)   …            PC#10 worker(s)
   (Docker, stateless)  (Docker)         (Docker)                      (Docker)
   claim job → run full pipeline → advance status → heartbeat → (already) sweep stale jobs
```

- **One DB for all PCs.** Workers hold no local DB. They find the head via a single `DB_URL` (explicit env, or a discovery file like NETS's `head-address.txt` so the head can move).
- **Worker = our existing pipeline, containerized.** Run the API with `worker_concurrency=0`; run workers as standalone Docker containers. Set `worker_concurrency` per PC for in-PC parallelism.

---

## 3. How a batch flows (end to end)

1. Operator opens the **fleet dashboard** and pastes a **Notion subject URL**. ⚠ **Resolving that into a lesson list is a multi-step, fallible pipeline, not a lookup** (see §5): download the subject's textbook PDF → ingest → **TOC extraction** (the project's most fragile step — >20 MB cap, glyph-subset fonts, scanned books; worklogs 0034/0035/0036/0040/0043) → the resulting `toc_entries` **are** the lessons. The dashboard shows this step's progress and its failure modes.
2. Operator ticks the lessons, picks **generation mode (CLI or Google API)** + an optional budget/guardrail, and hits Start. The head writes **one `batches` row** (carrying `generation_mode`) + **N `pending` `homework_jobs`** (one per `toc_entry`), each tagged `batch_id`. No owner/host pinning — any PC may claim any job (work-stealing).
3. Any worker PC claims a job (`FOR UPDATE SKIP LOCKED`), runs the full pipeline, writes `phase_outputs`, advances the job to `done`/`failed`, keeping the claim fresh via `touch_claim`.
4. Job completion rolls into the **batch aggregate** (`done/failed/running`, spend, ETA), recomputed on read.
5. A worker dies → its in-flight job goes stale → **an existing worker's sweep reclaims it** (`reclaim_stuck_jobs` → `pending`); the pipeline rebuilds phase rows via `create_or_reset` on the next run. (Already shipped.)
6. The dashboard shows the batch funnel + fleet health and lets the operator **pause / cancel / retry** the batch (on our existing per-job cancel) and **drain** a PC.
7. Operator reviews finished packets + failures in the **existing review console**. The interactive single-job path is untouched.

*(Phase 4 adds: each paid Gemini call checks out a credential from the pool, checkpoints cost to the ledger before spend; crossing the cap trips the kill-switch.)*

---

## 4. Data model deltas

All additive; nothing existing is removed.

**`homework_jobs`** (extend) — `claimed_by` + `claimed_at` **already exist**; the lease is **already implicit** (`claimed_at` + `reclaim_stale_seconds`) and refreshed by `touch_claim`. So **do not add `lease_expires_at`/`heartbeat_at`** unless they demonstrably earn their keep — default to reusing the existing mechanism, not a parallel one. **Genuinely new:** `batch_id UUID NULL REFERENCES batches(id)`. *(Phase 4 only)* `cost_usd`, and a budget cap (likely inherited from the batch).

**`batches`** (new) — the aggregate the operator supervises:
- `id UUID PK`, `created_at`, `created_by`, `source` (Notion subject URL + resolved subject/grade/lang + originating `book_id`), `generation_mode` (`cli` | `api`), `status` (`active`/`paused`/`done`/`cancelled`).
- Rollups **computed on read** (recompute, not trigger — locked, §1 scale makes this simpler and sufficient): `total/done/failed/running` counts, `spend_usd` *(Phase 4)*, `budget_cap_usd`.
- The launcher writes each child job's **`provider`** from the batch's `generation_mode` at insert (cli→`claude` + failover chain; api→the new Gemini-API provider); the pipeline keys off `job.provider`, not `generation_mode`. **Judge is always Opus regardless of mode.**

**`workers`** (new) — fleet registry/liveness:
- `pc_id TEXT PK`, `last_heartbeat`, `status` (`online`/`draining`/`offline`), `notes`. Online = `last_heartbeat >= NOW() - <threshold>`.

**`agent_usages` → cost ledger** *(Phase 4)* — evolve into / alongside `cost_events`: add real `cost_usd`, `idempotency_key` (never-pay-twice), and an `AFTER INSERT` trigger that rolls cost into job + batch and flips to `failed` at the cap.

**Credential pool** *(Phase 4)* — port NETS's `gcp_projects`, `service_account_keys`, `project_model_quota`, `project_live_state`, + the `project_cost_today` rollup view. Caps: per-(project,model) RPM/concurrency, project-wide daily, lifetime; 429 cooling.

---

## 5. Components

- **Notion → lessons resolver (⚠ first-class, fallible — NOT a cheap reuse).** Our existing `notion_fetch.py` only goes subject → **one textbook PDF** (`download_textbook`); endpoints are just `/grades` + `/grades/{id}/subjects`. **There is no lesson enumeration.** Lessons = a book's `toc_entries`, which exist only after `download → ingest_pdf → async TOC extraction → list toc_entries`. So the resolver is a pipeline: (1) resolve subject → download + ingest + **extract TOC** (may need the >20 MB / scanned-book / glyph fallbacks — the project's most fragile step), (2) surface `toc_entries` as the lesson list, (3) operator picks. **TOC-extraction failure is a first-class state** the launcher and dashboard handle, not an exception.
- **Batch launcher** (port of NETS commit `205cf6f`): `POST /jobs/batch` inserts one `pending` job per picked `toc_entry` + a `batches` row. **No owner/host pinning at all** — pure work-stealing (our simplification over NETS, which round-robins `owner_pc`; host-affinity is cut, §8 — and no such column exists in our schema). The launcher **translates the batch's `generation_mode` into each job's `provider` at insert** (cli→`claude` + failover chain; api→the new Gemini-API provider) — the pipeline reads `job.provider`, **not** `generation_mode`.
- **Worker** (existing pipeline, containerized): claim → run DAG → advance, `touch_claim` keeps the claim fresh. Standalone entrypoint already exists; **net-new = a Docker image + a `DB_URL`.** API runs with `worker_concurrency=0`.
- **Self-healing:** already shipped (0031) — every worker sweeps + any worker reclaims all stale jobs. **Net-new = the `workers` registry table + a head-side liveness view only.**
- **Fleet dashboard:** new panels — batch funnel + recompute rollups, fleet/PC cards (liveness, current job), controls (launch / pause / cancel / retry / drain), and the Notion→TOC resolver UI with its failure states. Auth gated like NETS (single admin token; reads public). Existing review console stays for content review.
- **Discovery:** each worker resolves the head from `DB_URL` env or a discovery file (movable head, ≤60s reconnect).
- *(Phase 4)* **Gemini-API provider:** implemented as a **new provider inside the existing CLI router** that calls the SDK behind a flag, returning the same `(text, usage)` envelope — so `run_phase`, the other providers, and the judge stay **path-agnostic** and the "no-SDK" invariant is broken in **exactly one place**. Plus the **credential pool**, **cost ledger + kill-switch**, and **never-pay-twice idempotency**.

---

## 6. Phased delivery

Phases 0–3 are the simple automation core on our **current** generation path (subject to §1a). Phase 4 is the isolated real-money layer.

- **Phase 0 — One DB + Dockerized worker.** Make Postgres the network-reachable head; package the existing standalone worker as a Docker image reading one `DB_URL`; run API with `worker_concurrency=0`. Prove: two containers pull from one DB with no contention.
- **Phase 1 — Fleet visibility.** `workers` registry table + heartbeat + head-side liveness view. **(Reclaim already exists — not rebuilt.)** Prove: kill a worker mid-job → an existing worker's sweep reclaims it; the dashboard shows the dead PC.
- **Phase 2 — Batch automation.** `batches` table + recompute rollups; the **fallible Notion→TOC lesson-resolution pipeline** (§5) with explicit failure handling; `POST /jobs/batch`. Prove: pasting a subject URL extracts its TOC (or fails cleanly) and enqueues one job per lesson; rollups track progress.
- **Phase 3 — Fleet dashboard.** Launch/watch/control + fleet view, **with the CLI/API mode toggle in the launch UX**. Prove: operator runs a real CLI-mode batch end-to-end across PCs from one screen (claude-first + failover, Opus judge).
- **Phase 4 — Real-money Gemini-API layer (isolated; plan as FOUR deliverables, not one).**
  - 4a. **Gemini SDK provider** behind the router flag (envelope-compatible). **Real seam (don't take "one place" literally):** the `Provider` base is subprocess-shaped (`build_argv → _spawn → create_subprocess_exec`, `agent.py:287/329`), so an in-process SDK call needs a **dispatch branch in `run_phase`/`_spawn` (`agent.py:561/287`)**, not merely a new `Provider` subclass. The invariant held is that everything **above** `run_phase` (pipeline, judge, failover) stays path-agnostic via the shared `(text, usage)` envelope.
  - 4b. **Credential pool** (projects/keys/quota/caps/429 cooling). **Verify the table schema against the real NETS repo at port time — not this spec's recollection (see ⚠ at top).**
  - 4c. **Cost ledger + kill-switch** (real `cost_usd`, batch rollup, cap → `failed`).
  - 4d. **Never-pay-twice** idempotency (checkpoint-before-spend + idempotency key).
  - This is the only place that reverses CLAUDE.md's "no-SDK" invariant (fleet path only; interactive console keeps the CLI router). CLAUDE.md + `pyproject.toml` updated here.

> Phases 0–3 run in **CLI mode** (claude-first + failover on the 3 dedicated accounts, Opus judge), so the fleet is fully provable before the API pivot. **Phase 4 (API mode) is selectable per batch and testable now with existing GCP credits**, so it's wanted reasonably soon — but CLI mode is the primary near-term path. Phase 4 stays its own spec-level effort (4a–4d).

---

## 7. Decisions locked (from brainstorm + review)

1. **Operator-defined batches** — operator sets scope; fleet runs it hands-off.
2. **One centralized DB; no per-PC DB.** Workers stateless.
3. **Workers run in their own Docker.** No Swarm.
4. **Build into the homework app**, porting NETS infra patterns (no codebase merge).
5. **Per-batch generation mode (`cli | api`)** chosen at launch. CLI = `claude`-first failover across CLI providers (3 dedicated Max accounts across 10 PCs); API = Google Cloud / Gemini API (Phase 4). Both coexist; interactive console unchanged.
6. **Judge = always Claude Opus** in both modes (dedicated fleet accounts remove the lockout concern; no `JUDGE_PROVIDER` override).
7. **Batch rollups = recompute-on-read** (not triggers) — sufficient and simpler at our scale.
8. **Phase 4 = a new provider inside the router**, with the SDK dispatch branch at `run_phase`/`_spawn` (`agent.py:561/287`); everything above `run_phase` stays path-agnostic via the `(text, usage)` envelope.
9. **Dashboard controls:** define/launch, pause/cancel/retry, see/manage PCs, review output + spend.

---

## 8. Out of scope / deliberately not copied

- Docker Swarm; `stack.yml`-style fleet orchestration.
- Host-affinity (`owner_pc`/`assigned_pc`), elastic cross-host artifact stealing, per-stage workers.
- A new reclaim engine (already shipped in 0031).
- Autoscaler (fixed worker count per PC initially).
- TLS on the DB hop (network isolation initially, like NETS) — revisit if PCs span untrusted networks.
- Encryption-at-rest for SA keys is **in scope for Phase 4b** (NETS skipped it — we won't), but not before.

---

## 9. Open questions (resolve at plan time)

1. **PgBouncer now or later?** 10 PCs × `worker_concurrency` × in-pipeline DAG fan-out could exceed Postgres `max_connections`. Likely a small add-on only if we hit the ceiling.
2. **`lease_expires_at`/`heartbeat_at` columns** — do they earn their keep over the existing implicit lease, or do we reuse `claimed_at` + `reclaim_stale_seconds` as-is? (Lean: reuse as-is.)
3. **Phase 4 timing** — CLI mode is primary near-term; API mode is selectable per batch and testable now with existing GCP credits. When to build 4a–4d (its own spec).
4. **Claude account ↔ PC mapping** — how the 3 dedicated accounts are distributed across the 10 PCs and mounted into each worker container (operator setup; ~3–4 PCs/account).

---

## 10. Acceptance

- Phases 0–3: a real **CLI-mode** batch of ≥2 lessons (resolved via the fallible TOC pipeline) runs to completion across ≥2 worker containers pulling from one DB (claude-first + failover, **Opus judge**), survives a mid-job worker kill (existing sweep reclaim), and is launched + monitored + cancel/retried from the dashboard.
- **Phase 3 also includes a real Claude cap-hit smoke**: an exhausted fleet account must emit a usage-limit message the classifier treats as a wall → fail over to the next provider. (The CLI-emits-a-classifiable-message link is unprovable on paper — verify it live, don't assume.)
- Phase 4: a real Gemini-API smoke generates one packet, writes correct per-call `cost_usd`, and a deliberately low cap trips the kill-switch (job → `failed`, no further spend). Per CLAUDE.md, generation-affecting changes require a real API smoke as the proof.
