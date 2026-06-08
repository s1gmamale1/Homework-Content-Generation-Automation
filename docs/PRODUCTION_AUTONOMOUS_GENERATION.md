# Production-Ready Autonomous 24/7 Generation — Findings

> Advisory / design findings (not a spec or plan). Captures the analysis of what it
> takes to make the content-generation pipeline production-ready and run autonomously
> at maximum *sustainable* capability. Turn any pillar into a brainstorm → spec when
> ready. Date: 2026-06-03.

## Verdict

The **plumbing** is ~80% there (Postgres queue with `SELECT … FOR UPDATE SKIP LOCKED`,
restart-safe workers, orphan reclaim, Docker/Traefik/watchtower). The **autonomy** is
0% there: today every job is human-triggered (upload PDF → pick a TOC entry →
`POST /generate`). Nothing decides *what to generate next on its own*. That driver is
the centerpiece to build; everything else is hardening.

A second, equally important finding: the binding throughput constraint is **not**
request-per-minute API throttling — the `claude` provider runs on a **Claude Max $200
subscription** (OAuth/CLI mode), whose limits are *allocation-based*. That reshapes
"max capability" from "max concurrency" into "budget-aware pacing + provider failover".

---

## 1. The missing piece — an autonomous driver

A loop that keeps the queue full without a human:

- **Enumerator** — walk every `book × toc_entry × difficulty` with no successful job
  (a `LEFT JOIN` against `homework_jobs`) and enqueue the gaps through the same path the
  API uses.
- **Backfill controller** — keep N jobs `pending` at all times; top up when the queue
  drains. This is what makes it "24/7".
- **Ingestion feeder** — Notion **Phase 2 (pull)** is the upstream: auto-fetch new
  textbooks from the subject pages → enqueue their lessons. Without it, "autonomous"
  still needs a human to upload books.
- **Priority / fairness** — round-robin across subjects/grades so one book doesn't
  starve the rest.

This is the difference between "a tool" and "a content factory".

---

## 2. Throughput / max capability

- Tune the two real limits **together** — `WORKER_CONCURRENCY` (concurrent jobs, default
  4) **and** `GEMINI_MAX_CONCURRENCY` (process-wide concurrent CLI subprocesses, default
  8). Raising jobs alone just makes more jobs contend for the same subprocess pool.
- **Gotcha:** `config.py` defines `agent_max_concurrency` and labels
  `gemini_max_concurrency` "DEPRECATED", but the live semaphore at `agent.py:266` still
  reads `gemini_max_concurrency`. So `AGENT_MAX_CONCURRENCY` is currently a **dead
  setting** — set `GEMINI_MAX_CONCURRENCY`. (Both default 8, so behaviour is fine today;
  the trap is tuning the wrong one.)
- **Horizontal:** `worker_concurrency=0` + separate worker pods (compose already has a
  scaled worker profile).
- **Provider load-balancing** — spread jobs across all five CLIs (provider is currently
  fixed per job). Under subscription mode this is the single highest-leverage move (see §4).

---

## 3. Reliability / self-healing

- **Stuck `pending` jobs** with attempts exhausted are never failed (WISHLIST, e.g.
  `2848dbcb`). A 24/7 system *will* accumulate these → sweep to `failed` + dead-letter.
- **Retry with exponential backoff** + a max, not just orphan reclaim.
- **Circuit breaker per provider** — when a CLI starts failing/throttling, stop feeding
  it instead of burning the queue.
- **Notion silent per-subject skip** (Kimyo incident): an unmapped subject just logs
  "no subject-page mapping … skipping" and the homework never pushes — must be surfaced,
  or autonomous output silently vanishes.
- ~~`reclaim_stale_seconds`~~ — **DONE (worklog 0031).** A separate `reclaim_stale_seconds`
  setting (default 120s) plus a per-job heartbeat now reclaim orphaned `running` jobs fast,
  without shortening the real execution timeout.

---

## 4. Subscription-mode constraints (the real ceiling)

The `claude` provider runs on a **Claude Max $200** plan via OAuth/CLI, **not**
pay-per-token API. Therefore:

- **No RPM *allocation* throttling** — usage draws from the plan's allocation, not a
  requests-per-minute API quota. **But** there is a separate **transient server-side
  throttle** ("Server is temporarily limiting requests · *not your usage limit*"),
  observed after a sustained ~50-min heavy run — Anthropic shedding load. It's short-lived
  and clears with a brief backoff + retry. So there are **two distinct throttle classes**
  (see implications below).
- **Shared pool** across Claude Code, claude.ai chat, and Cowork — the factory competes
  with the user's own dev/chat usage on the same budget.
- **Rolling 5-hour limit** + **two weekly limits** (reset every 7 days): one overall, one
  specific to the **most-advanced model (Opus)**. `/status` shows remaining (interactive).

**Implications for "24/7 at max capability":**

- **True flat-out 24/7 on one subscription is not achievable** — weekly caps are designed
  to stop sustained max-rate use. You generate hard for some hours, hit the weekly wall,
  then idle until the 7-day reset.
- **Pace to the weekly budget** (~weekly ÷ 7), not max concurrency.
- **Spread across the other three providers** — kimi/codex/gemini are *independent*
  allocations, so provider rotation is how you get sustained volume without one weekly cap
  halting everything.
- **Tier-route by model** — reserve the Opus weekly cap for phases that need it (e.g.
  boss-arena reasoning); push the rest to Sonnet, or the *advanced-model* weekly cap binds
  first and stalls the pipeline while overall budget still has room.
- **The factory competes with you** — running 24/7 on the shared pool can exhaust the
  weekly cap and block the user's own Claude Code work (incl. dev on this project). The
  driver should respect a reserve (e.g. pause generation at ~80% weekly).
- **The headless CLI likely can't read remaining budget** (`/status` is interactive). The
  system learns by **hitting the wall** → needs (a) **reactive failover** (detect
  limit-reached → pause Claude, fail over, resume after reset) and (b) **local estimation**
  off `agent_usages` rows to self-throttle *before* the wall so jobs don't die mid-flight.
- **Two throttle classes must be distinguished** (the governor branches on which):

  | Throttle | Signal | Recovery |
  |---|---|---|
  | Transient server limit | "temporarily limiting… not your usage limit" | short **exponential backoff + retry** (seconds–minutes) |
  | Allocation wall | 5-hour / weekly cap reached | **wait for reset** (hours–days) or **fail over** to another provider |

  Treating a transient throttle as an allocation wall pauses Claude needlessly for hours;
  treating an allocation wall as transient hammers it with futile retries.

Net: the autonomous driver's scheduler shifts from "max concurrency" to **budget-aware
pacing + provider failover** — a budget-governor layer on top of the enumerator/backfill.

---

## 5. Observability — non-negotiable for unattended ops

Can't run 24/7 blind. Need queue depth, throughput/hr, success rate, per-provider latency
& error rate → metrics + **alerting** (page when success-rate drops or the queue stalls).
The `/usage` page tracks *local* token consumption, not health.

---

## 6. Quality gates — mostly resolved; one safeguard remains

The original risk (no human + a warn-only validator → un-reviewed content straight to
Notion) is **largely closed**: the deterministic warn-only `phase_validator` was replaced
by an **LLM judge with teeth** (`phase_judge.py`, worklog 0037) that grades every content
phase against its prompt contract and **regenerates once on a MAJOR verdict** (minor nits
warn-only). The **Effort B** faithful-prompt reshape also shipped (worklog 0030) — so
"finish the md-per-phase flip" is **done**. Two things still matter before unattended
mass-gen: **(a)** write autonomous output to a **draft/staging Notion state, not straight
to live** (a bad batch otherwise pollutes the real workspace); **(b)** a periodic
**sampling spot-check** (the judge has teeth but is one LLM call per phase). **Note:** the
judge defaults to **claude-opus** (0037), so judging now spends the reserved claude-Max
pool — compounds the provider-isolation concern in #2 of the controller-review notes.

---

## 7. Infra hardening (the hard truths)

- **CLI auth is the #1 production risk.** The five providers are interactive-auth CLI
  subprocesses. Running them in N pods, 24/7, re-authenticating on token expiry, is more
  operationally fragile than the queue. Solve this before scaling pods.
- **Local-disk PDFs block multi-pod.** `var/books/<id>/source.pdf` is on local disk;
  horizontal workers need shared object storage (S3/GCS) first.
- Secrets management, DB backups, migrations-on-deploy (entrypoint already runs
  `alembic upgrade head`), health checks, autoscaling.

---

## Recommended order

1. **Quality foundation — DONE** (md-per-phase flip: LLM judge with severity gate, 0037, + Effort B prompts, 0030). Remaining safeguard before mass-gen: draft/staging Notion output.
2. **Harden the 3 reliability bugs** (stuck `pending`, retry/backoff, Notion silent skip).
3. **Build the curriculum driver** (enumerator + backfill + budget-governor + provider failover).
4. **Then scale** (object storage → CLI-auth-at-scale → worker pods → observability/alerting).

Building the driver before reliability/quality just automates the production of broken output.

---

## Controller review additions (2026-06-03)

Agreements: the driver-is-the-centerpiece framing, the allocation-not-concurrency
ceiling, and the quality → reliability → driver → scale order are all correct. Five
refinements / corrections:

1. **§6 quality gate — now largely RESOLVED (updated 2026-06-06).** The earlier state
   (Effort B deferred; validator shipped warn-only, `phase_validator.RULES` empty) no longer
   holds: **Effort B shipped** (worklog 0030) and the warn-only `phase_validator` was
   **deleted and replaced by the LLM judge** (`phase_judge.py`, worklog 0037) — a severity
   gate that **regenerates on MAJOR** verdicts. So the "quality foundation" is built. The
   remaining safeguards before unattended mass-gen: write autonomous output to a
   **draft/staging state, not straight to live Notion** (a bad batch otherwise pollutes the
   real workspace), plus an optional **sampling-QA** spot-check. Caveat: opus-judging draws
   on the reserved claude-Max pool (see #2).

2. **Provider isolation > a reserve threshold (elevate §4).** The factory runs on the SAME
   Claude Max sub used to *develop this project*, so flat-out runs can lock the user out of
   their own Claude Code. "Pause at ~80% weekly" is insufficient. Design constraint: the
   factory runs **primarily on kimi/codex/gemini and treats `claude` as reserved-for-the-user**
   (or a separate account). Provider rotation is **budget isolation**, not just throughput.

3. **Local budget estimation is weaker than §4 implies.** `agent_usages` token counts can't
   denominate the real ceiling: **kimi reports 0 tokens** (stream-json gap) and the
   **claude-sub limit is plan-allocation, not token-denominated**. A token-estimator would be
   blind for kimi and meaningless for claude-sub. The governor must pace on **call-count +
   duration heuristics + reactive failover**, not true token math.

4. **Empirical data (md-per-phase live smoke, Kimyo §1, claude/sonnet-4-6, 2026-06-03):**
   one section = **~17 min / 9 phases**; **boss-arena alone ~11 min**; one boss-arena attempt
   **failed mid-run with "socket connection closed unexpectedly" then auto-recovered on
   retry** — a live instance of the **transient-throttle class** (§4 table) and a concrete
   argument for the per-provider circuit breaker + distinguishing the two throttle classes.
   At ~17 min/section, a full curriculum (7 subjects × ~20 sections × difficulties) is a very
   large, multi-day queue even before weekly caps — reinforces marathon-pacing over
   concurrency.

5. **Step 1 of the recommended order now explicitly includes** finishing the deferred
   **Effort B (subject-specific prompt build)** + **giving the validator teeth / sampling-QA**.
   And before writing driver code, the **first thing to design is the budget-governor +
   provider-isolation policy** — that determines whether 24/7 is even viable on this setup.
