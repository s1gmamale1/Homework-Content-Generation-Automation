# Deployment

The codebase is **one Docker image, two run modes**:

```
$ uvicorn main:app                  # API + embedded worker (default)
$ python -m app.services.worker     # standalone worker (no HTTP)
```

Same image serves both. Pick a topology by setting environment variables.

---

## TL;DR — local in 30 seconds

```bash
cp .env.example .env
# edit .env: set a strong AUTH_TOKEN (and transport=api keys only if needed)
docker compose up
```

The shipped `docker-compose.yml` **pulls a prebuilt GHCR image**
(`ghcr.io/ganiyevuz/class-homework-builder:latest`, `pull_policy: always`) — no local
build. It loads `.env` via `env_file`, constructs `DATABASE_URL` pointing at the in-compose
`postgres` (`@postgres:5432/${POSTGRES_DB:-edu_homework}`), and runs API + SPA + embedded
worker. Migrations run automatically on container start via `docker-entrypoint.sh` (see
Migrations below). The api container is fronted by **Traefik** (TLS via the `le` resolver,
host `${APP_HOST}`, loadbalancer port **8000**) and auto-redeployed by **Watchtower** when a
new image tag is pushed — so in a Traefik deployment you reach it at `https://${APP_HOST}`,
not `localhost:8000`. (For a bare local run without Traefik, publish port 8000 yourself.)

---

## Required environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://...` (asyncpg driver, not psycopg) |
| `GEMINI_API_KEY` | no | — | Only for `transport=api` gemini jobs (or use the Vertex SA pair `GOOGLE_APPLICATION_CREDENTIALS`+`GOOGLE_CLOUD_PROJECT`). Default `cli` jobs need no key. Read from `os.environ` by the api transport — the same-named config field is vestigial. **Primary path is now the Fleet → Keys UI** (see the note below the table): workers boot **keyless** and are handed a Vertex SA key live, assigned by hostname; these env vars remain a fallback/legacy option. |
| `ANTHROPIC_API_KEY` | no | — | For `transport=api` claude jobs. The worker computes **per-role** api-readiness flags at startup (`worker._compute_capabilities`: `can_claude_api`, `can_gemini_api`, `judge_api_ok`, `judge_fallback_api_ok`, `extract_api_ok`) and `claim_next_job` ANDs each job's *resolved* per-role transports against them — so a worker can claim *some* api jobs without *all* creds (e.g. an api-content gemini job with cli judge+extract needs no `ANTHROPIC_API_KEY`). |

> **Key distribution — Fleet → Keys UI (primary):** Vertex/SA credentials are now distributed to
> workers **live from the head's Fleet → Keys page** — upload the SA key on the head, assign it to a
> worker by hostname, and the worker (which boots **keyless**) picks it up at runtime. The
> `GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CLOUD_PROJECT`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`
> env vars above still work as a **fallback/legacy** path, but the Keys UI is the recommended
> mechanism for a fleet.
| `GEMINI_MODEL` | no | `gemini-2.0-flash-exp` | Vestigial (unread by the runtime). The *extract pin* `EXTRACT_MODEL` is separately `gemini-2.5-flash`. |
| `AUTH_TOKEN` | **yes** (except explicit local dev) | empty / startup refusal | Every comma-separated member must be a strong ASCII URL-safe token. Empty, weak, malformed, duplicate, or mixed weak+strong values refuse head and standalone-worker startup. Rotate an existing fleet with [`docs/runbooks/operator-token-rotation.md`](./runbooks/operator-token-rotation.md); never bridge with `123,<strong>`. |
| `ALLOW_INSECURE_LOCAL_AUTH` | no | `false` | `true` permits only the exact empty-token state for explicit local development. It never opens any `/api/v1/sa-keys*` route, which remains header-only and returns 503 without configured tokens. |
| `WORKER_CONCURRENCY` | no | `4` | Embedded worker job concurrency. Set `0` in API-only pods. |
| `COST_CAP_BATCH_USD` | no | `0` | Per-batch API spend cap in USD. `0` disables the check. When a batch's api spend exceeds this, the budget monitor pauses it (`batches.paused_at`/`paused_reason`) so no further api jobs from that batch are claimed. The pause reason is set to `"batch-cap"`. |
| `COST_CAP_FLEET_DAILY_USD` | no | `0` | Fleet-wide rolling 24h api spend cap in USD. `0` disables. When the 24h api spend across all jobs exceeds this, the budget monitor sets the `budget_state` singleton's `api_paused_at`, blocking all api-transport jobs across the entire fleet. The pause reason is set to `"fleet-daily-cap"`. |
| `COST_CHECK_INTERVAL_SECONDS` | no | `60` | How often (seconds) the budget monitor loop runs its spend checks. Lower values catch overruns sooner; at `60` the worst-case overrun is roughly one job's api cost above the cap. |
| `JOB_TIMEOUT_SECONDS` | no | `1800` | Hard ceiling per job. (`.env.example` overrides it to `600`.) |
| `QUEUE_MAX_ATTEMPTS` | no | `3` | Retries before terminal failure. |
| `QUEUE_BACKPRESSURE_LIMIT` | no | `50` | Queue depth → 503. `0` disables. Note this counts only jobs that are *due* (`scheduled_at <= now()`), so a staggered batch launch never inflates it. |
| `BATCH_LAUNCH_WAVE_SIZE` | no | `6` | How many jobs a batch launch makes claimable at once. The rest get `scheduled_at` stamped one interval per wave into the future, so a big launch stops firing every lesson's API calls in the same instant. Sized from the measured 2026-08-11 incident: per-job peak fan-out is 5.54 calls, so 6 × 5.54 ≈ 33 against `CREDENTIAL_MAX_CONCURRENT_GEMINI=32`. A launch of this many lessons or fewer is not delayed at all. `0` disables the stagger. |
| `BATCH_LAUNCH_WAVE_INTERVAL_SECONDS` | no | `60` | Seconds between waves. Clears the `extract` phase (measured p50 13.1s, max 16.1s) plus one content call (avg 35.9s) so consecutive waves cannot stack. `0` also disables the stagger. |
| `AGENT_MAX_CONCURRENCY` | no | `8` | ⚠️ **Must be ≥ 1** — `0` is rejected at startup, because it would build an `asyncio.Semaphore(0)` and every model call would block forever with no error and no log (the host would claim jobs, make no calls, look healthy, and lose each job to `JOB_TIMEOUT_SECONDS`). **The live knob** — process-wide cap on *all* CLI subprocesses. `agent._effective_concurrency()` reads this first; when it is left at its default (8), `GEMINI_MAX_CONCURRENCY` is used as a fallback so existing configs are not broken. Tune to your RPM tier (`AGENT_MAX_CONCURRENCY ≤ your_RPM_tier / 4`). **Per-process** (module global), so the real fleet cap is `N_processes × this`, not deployment-wide. |
| `GEMINI_MAX_CONCURRENCY` | no | `8` | ⚠️ **Must be ≥ 1** (same `Semaphore(0)` hazard as above — it feeds the same semaphore whenever it wins). **Deprecated fallback** — honoured only when `AGENT_MAX_CONCURRENCY` is left at its default (8). Kept for backwards-compat with existing `.env` files. Prefer `AGENT_MAX_CONCURRENCY` for new deployments. |
| `RATE_LIMIT_MAX_RETRIES` | no | `4` | Reactive 429 backoff (`concurrency-knob-1` ph1): how many times `agent._spawn` retries a call that came back with a transient rate-limit (`429` / `RESOURCE_EXHAUSTED` / `overloaded_error` / "too many requests"; **not** auth `401/403` or `MAX_TOKENS`) before returning the failure to the phase-level failover. `0` disables the in-place retry. |
| `RATE_LIMIT_BASE_DELAY_SECONDS` | no | `2.0` | First backoff delay; grows exponentially per attempt (with jitter). The backoff `asyncio.sleep` holds **no** concurrency slot. |
| `RATE_LIMIT_MAX_DELAY_SECONDS` | no | `30.0` | Ceiling on the exponential backoff delay. The same backoff also covers **transient connection/DNS errors** (`getaddrinfo`/`NameResolution`/`HTTPSConnectionPool`/`WinError 10053/121/1232`/`connection aborted`/`timeout`) since `fleet-net-1` (worklog 0089), so a flaky-network worker throttles-and-retries instead of dropping the call. |
| `SESSION_LIMIT_STRATEGY` | no | `pause` | Fleet-wide default for what a worker does on a Claude **session-limit** (`fleet-session-limit-autopause-1`, worklog 0089). `pause` = parse the stated reset time, requeue the job (no attempt burned), self-cooldown that worker until reset, auto-resume. `switch` = fail the limited role over to a non-limited model down `FAILOVER_PROVIDER_ORDER` and keep generating. Per-batch `session_limit_strategy` (`pause`/`switch`/`inherit`) overrides this; `inherit` (the batch default) falls back to this env value. Must be `pause` or `switch`. |
| `SESSION_LIMIT_DEFAULT_TZ` | no | `America/Chicago` | IANA tz used to interpret a session-limit reset clock-time (`resets 12:50am`) when the message states no tz. |
| `SESSION_LIMIT_DEFAULT_COOLDOWN_SECONDS` | no | `3600` | Fallback worker self-cooldown when a session-limit's reset time can't be parsed. |
| `REGENERATION_ENABLED` | no | `false` | Master flag for versioned homework regeneration. Off ⇒ every `/api/v1/regeneration*` route returns **404** (deliberately not 403 — a stale SPA must not be able to detect that the routes exist). Head-only; workers need neither regeneration flag. |
| `REGENERATION_PUBLISHER_ENABLED` | no | `false` | Second, independent flag for the loop that publishes `Homework V{n}` Notion sibling pages. Separate from the flag above so generation can be exercised with delivery dark. **The loop requires BOTH flags AND a usable Notion destination** — `main.py` starts it only under `regeneration_enabled and regeneration_publisher_enabled`, and then only when `NOTION_ENABLED` is true with a `NOTION_API_KEY` shaped `ntn_`/`secret_`; this flag alone (master flag off) starts nothing and logs no start line, while a missing Notion destination logs `Regeneration publisher NOT started: <reason>`. **Enable on the ONE designated head/API process** (the claim protocol is safe if two run it, but don't). `POST /regeneration/campaigns/{id}/approve` and `POST /regeneration/targets/{id}/retry-publication` return `409 publisher_disabled` while this flag is off and `409 notion_unavailable` when the Notion destination is missing — so the flag-on order is schema → Notion prerequisite → `REGENERATION_ENABLED` → this → SPA rebuild (UI last). Drafting, estimation, campaign creation, canary generation and `retry-generation` are ungated and work with delivery dark. |
| `REGENERATION_PUBLISHER_INTERVAL_SECONDS` | no | `30` | Idle sweep interval. A pass that did work loops straight back, so a backlog drains at Notion's pace, not at this interval. Delivery is **serial** — one pass publishes at most one page. |
| `REGENERATION_PUBLISHER_LEASE_SECONDS` | no | `300` | Durable publication claim lease; keep well above a realistic Notion write (page + children + PDF upload). On shutdown the publisher gets a **30s** grace to finish the target it is on; a target cancelled past that keeps its lease until this expires, and only then can another pass adopt the half-written page by its marker. Don't expect a restarted head to resume a killed delivery immediately. |
| `REGENERATION_PUBLISHER_MAX_ATTEMPTS` | no | `5` | Automatic delivery attempts before a target parks in `publication_failed` for an operator. Must be ≥ 1. |
| `REGENERATION_PUBLISHER_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | no | `60` / `3600` | Exponential backoff between delivery attempts, and its ceiling. |
| `REGENERATION_LAUNCH_WAVE_SIZE` / `_INTERVAL_SECONDS` | no | `4` / `60` | Launch stagger for a campaign's bulk wave. Same mechanism as `BATCH_LAUNCH_WAVE_*`, deliberately more conservative because a regeneration wave re-runs whole snapshots on top of whatever the fleet is already generating. Either at `0` disables the stagger. |
| `REGENERATION_MAX_CAMPAIGN_TARGETS` | no | `500` | Maximum eligible lesson/language targets one campaign may own. Ineligible discovery rows remain visible but do not count. Split larger work into separately reviewed campaigns. |
| `REGENERATION_MAX_DISCOVERY_LINEAGES` | no | `1000` | Candidate-lineage workload bound before discovery fans out into indexed per-lineage source checks. The query fetches at most this value + 1 and refuses without silently truncating; narrow by book or lesson. |
| `APP_GIT_REVISION` | **in a container, yes** (for regeneration) | *(empty)* | The commit this build was made from. Campaign creation stamps an immutable `app_git_revision` and resolves it: explicit request field → **this variable** → the process's own git checkout → structured `409 app_git_revision_unavailable` (refusal, never NULL). Blank counts as absent. The `Dockerfile` declares it as an `ARG` and re-exports it as `ENV`, and CI binds it to the built commit, so a GHCR image from `docker-publish.yml` already carries it. A hand-built image does not. Irrelevant on a bare-metal head from git — and there, a stale value in a shared `.env` **outranks** the checkout and mis-stamps campaigns, so leave it unset. |
| `MAX_FILE_MB` | no | `50` | Upload size limit. |
| `ENABLE_DOCS` | no | `false` | Swagger UI at `/docs`. Disable in prod. |
| `ALLOW_ORIGINS` | no | `*` | Comma-separated CORS allow-list. |

---

## Topology 1: single pod (API + embedded worker)

**Best for:** small to medium traffic (≤1K jobs/day). One process serves HTTP and runs the queue worker. Simpler ops.

```yaml
# All-in-one
services:
  api:
    image: ghcr.io/ganiyevuz/class-homework-builder:latest
    pull_policy: always
    environment:
      WORKER_CONCURRENCY: "4"   # embedded worker, 4 concurrent jobs
      # ... other env vars
```

This is what `docker compose up` runs by default.

## Topology 2: separate API + worker pods (horizontally scaled)

**Best for:** higher throughput, independent scaling, resilient deploys (rolling-restart API without dropping jobs).

The shipped `docker-compose.yml` already wires this as the **`scaled` profile** — an
optional standalone `worker` service (`python -m app.services.worker`, `RUN_MIGRATIONS=0`,
`deploy.replicas: 2`) gated behind `profiles: [ "scaled" ]`:
```yaml
services:
  api:                     # set WORKER_CONCURRENCY=0 in .env to disable embedded worker
    image: ghcr.io/ganiyevuz/class-homework-builder:latest
    pull_policy: always
    # RUN_MIGRATIONS defaults to 1 → this container runs the migrations

  worker:                  # only starts under `--profile scaled`
    image: ghcr.io/ganiyevuz/class-homework-builder:latest
    pull_policy: always
    command: ["python", "-m", "app.services.worker"]
    profiles: [ "scaled" ]
    environment:
      RUN_MIGRATIONS: "0"   # api migrates; worker doesn't
    deploy:
      replicas: 2           # or `--scale worker=N` on the CLI
```

To exercise this locally:
```bash
docker compose --profile scaled up
# set WORKER_CONCURRENCY=0 in .env to disable the api's embedded worker
```

---

## Migrations

Every deploy must run `alembic upgrade head` before the API starts. Three options, in order of preference:

**Option A — `docker-entrypoint.sh` on container start (already wired):**
There is **no** separate `migrate` service. The image's `docker-entrypoint.sh` runs
`alembic upgrade head` on every container start/restart, gated by **`RUN_MIGRATIONS`**
(default `1`). Alembic is idempotent at head, but to avoid concurrent migrators the
scaled topology sets `RUN_MIGRATIONS: "0"` on the standalone `worker` service so only
one container migrates. (In the shipped `docker-compose.yml` the `postgres` healthcheck
gates the api, so the entrypoint's migration runs against a ready DB.)
```yaml
services:
  api:
    image: ghcr.io/ganiyevuz/class-homework-builder:latest
    # RUN_MIGRATIONS defaults to 1 → entrypoint runs `alembic upgrade head`
  worker:
    image: ghcr.io/ganiyevuz/class-homework-builder:latest
    environment:
      RUN_MIGRATIONS: "0"   # only the api container migrates
```

**Option B — init container (Kubernetes):**
```yaml
spec:
  initContainers:
    - name: migrate
      image: class-homework-builder:latest
      command: ["alembic", "upgrade", "head"]
      env: [...]
  containers:
    - name: api
      image: class-homework-builder:latest
      command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Option C — release command (Render/Heroku/Fly):**
Set the platform's "release command" or "deploy command" to `alembic upgrade head`. Most PaaS run this once per deploy before swapping traffic.

---

## CI: build + push to GHCR

Workflow at `.github/workflows/docker-publish.yml`. Builds the image on every
push to `master`, on every `v*.*.*` tag, and on PRs against `master` (build-only —
no push). Multi-arch (amd64 + arm64), caches via GitHub Actions cache, attaches
SBOM and provenance. The image is published to the **GitHub Container Registry**
(`ghcr.io/<owner>/<repo>`, from `IMAGE_NAME = ${{ github.repository }}`).

### Setup

No manual secrets. The workflow logs in to GHCR with the built-in
`GITHUB_TOKEN` (`username: github.actor`) and already grants `packages: write`.
The only one-time step is making the published package visible/linked to the
repo if you want it public (GitHub → repo → Packages).

### Tag scheme (`docker/metadata-action`)

| Trigger | Tags pushed |
|---|---|
| `push` to `master` (default branch) | `<branch>` (`master`), `sha-<short-sha>`, `latest` |
| `push` tag `v1.2.3` | `1.2.3`, `1.2` |
| `pull_request` → `master` | (build only — verifies Dockerfile, no push) |

### Releasing a versioned build

```bash
git tag v0.1.0
git push origin v0.1.0
# CI builds + pushes 0.1.0 and 0.1
```

### Pulling the image into a deploy

```bash
docker pull ghcr.io/ganiyevuz/class-homework-builder:latest   # the image docker-compose.yml pulls
# or pin a digest for production:
docker pull ghcr.io/ganiyevuz/class-homework-builder@sha256:...
```

The pinned-digest form is the safer pattern in production deploys — it
prevents "the tag moved under us" surprises.

---

## Cloud recipes

### Render.com

1. Create a Postgres service. Note the `External Database URL` (rewrite to `postgresql+asyncpg://...`).
2. Create a Web Service from this repo:
   - Build command: (leave empty — Dockerfile handles it)
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Pre-deploy / release command: `alembic upgrade head`
   - Environment: paste from `.env.example`
3. (Optional, for scaling) Add a Background Worker service:
   - Start command: `python -m app.services.worker`
   - Same environment, with `WORKER_CONCURRENCY=4` (and set `WORKER_CONCURRENCY=0` on the Web Service)

### Fly.io

```bash
fly launch --no-deploy --copy-config
fly postgres create --name class-homework-builder-db
fly postgres attach class-homework-builder-db   # populates DATABASE_URL
fly secrets set GEMINI_API_KEY=...  AUTH_TOKEN=...
fly deploy
```

`fly.toml` release_command:
```toml
[deploy]
  release_command = "alembic upgrade head"

[processes]
  app = "uvicorn main:app --host 0.0.0.0 --port 8000"
  worker = "python -m app.services.worker"
```

Scale independently:
```bash
fly scale count app=2 worker=4
```

### Google Cloud Run

Cloud Run scales to zero, which is **bad for the embedded worker** (jobs in the queue won't get picked up while the service has 0 instances). Two options:

- **Option A:** Run API on Cloud Run with `WORKER_CONCURRENCY=0` and `min-instances=0`. Run a separate **always-on** worker on Compute Engine / Cloud Run Jobs (scheduled) / a small VM.
- **Option B:** Set `min-instances=1` on the API service so the embedded worker always has a process.

Option A is more cost-efficient at scale.

### AWS (ECS Fargate)

Two task definitions sharing the same image:
- `api` task: command `uvicorn main:app --host 0.0.0.0 --port 8000`, with `WORKER_CONCURRENCY=0`
- `worker` task: command `python -m app.services.worker`, with `WORKER_CONCURRENCY=4`

Behind an ALB, point the target group at the `api` task. Use RDS Postgres. Run `alembic upgrade head` as a one-shot Fargate task before each deploy.

---

## Pre-launch checklist

- [ ] `AUTH_TOKEN` set to a strong random value (or strong comma-separated values); `ALLOW_INSECURE_LOCAL_AUTH=false`
- [ ] Existing fleets use the [operator-token hard-cut runbook](./runbooks/operator-token-rotation.md): preserve any foreign global pause, drain, stage one token everywhere, operator-restart head first, then roll/attest every worker process
- [ ] `ENABLE_DOCS=false`
- [ ] Judge/extract model selection reviewed at **`/settings`** (DB-backed `launch_defaults` singleton, migration 0037). Seed defaults: judge = gemini/gemini-2.5-flash, extract = gemini/gemini-2.5-flash, `toc_transport=cli`. **⚠ On an all-Vertex head** (Vertex SA creds only, no gemini CLI OAuth): flip `toc_transport→api` at `/settings` immediately after first deploy, or book-upload TOC extraction fails. `EXTRACT_MODEL`/`EXTRACT_PROVIDER`/`JUDGE_MODEL`/`JUDGE_PROVIDER`/`EXTRACT_TOC_TRANSPORT` env vars are **deleted** — do not set them. (`GEMINI_MODEL` is also vestigial — nothing reads it.)
- [ ] Vertex/SA keys distributed via the head's **Fleet → Keys** UI (upload once, assign per worker hostname; workers boot keyless). Env-var creds (`GOOGLE_APPLICATION_CREDENTIALS`/`GOOGLE_CLOUD_PROJECT`/`GEMINI_API_KEY`/`ANTHROPIC_API_KEY`) remain a fallback/legacy option.
- [ ] `ALLOW_ORIGINS` set to actual frontend origin (not `*`) if API and SPA are on different domains
- [ ] Postgres has automated backups enabled (managed services usually do this; self-hosted needs `pg_dump` cron)
- [ ] Healthcheck endpoint `/health` reachable from your platform's liveness probe
- [ ] Migrations applied: `alembic upgrade head`
- [ ] Worker concurrency tuned to your tier:
      `AGENT_MAX_CONCURRENCY ≤ your_RPM_tier / 4` (the live knob — `agent._effective_concurrency()`; each job makes ~3-4 phase calls in parallel; `GEMINI_MAX_CONCURRENCY` is the deprecated fallback used only when `AGENT_MAX_CONCURRENCY` is left at default)
- [ ] If horizontally scaling: `WORKER_CONCURRENCY=0` on API pods so they don't double-claim jobs alongside dedicated worker pods
- [ ] Versioned homework regeneration left **off** unless separately authorized: `REGENERATION_ENABLED=false` and `REGENERATION_PUBLISHER_ENABLED=false`, and the SPA built **without** `VITE_REGENERATION_ENABLED=1`. Verify with a `404` from `GET /api/v1/regeneration/campaigns` and no `Regeneration publisher started` line in the boot log. Migration 0063 may stay applied (the tables are simply unused) — see [the regeneration runbook](./runbooks/versioned-homework-regeneration.md)

---

## Observability

The codebase emits structured loguru logs to stderr **and** to `var/server.log` (date-stamped format, rotated **daily** at midnight, retained 7 days — so a run is greppable in isolation, not conflated across a midnight boundary). In production, route them to your aggregator (Datadog, Loggly, CloudWatch, etc.). Key signals:

| Log line | What to watch for |
|---|---|
| `worker N starting` | Worker booted; should see one per pod on startup |
| `worker N claimed job=X` | Healthy queue throughput |
| `worker N reclaimed K stuck job(s)` | K should normally be 0; non-zero = a worker died mid-job and recovery worked |
| `worker N job=X TERMINAL failure` | Investigation needed; look at `last_error` column |
| `gemini phase.run billed` | Token usage per phase; track `fresh` column for cost trends |
| `pipeline complete \| total_s=N` | End-to-end job duration |
| `[job] token summary` | Per-job table; aggregate for cost dashboards |

For Prometheus-style metrics, the natural shape is:
- `queue_depth_gauge` from `jobs_repo.queue_depth()`
- `job_duration_histogram` from `pipeline.run` total_s
- `agent_calls_total` from the `agent_usages` table
- `agent_fresh_tokens_total` (sum of `prompt_tokens - cached_tokens`)

These aren't wired yet but the data is in the DB / logs — easy to add a `/metrics` endpoint when needed.

---

## Operational gotchas

**Connection pool sizing.** API-only heads (`WORKER_CONCURRENCY=0`) retain a 20+30 request pool. Worker processes (`WORKER_CONCURRENCY>0`) take an explicitly sized pool from `WORKER_DB_POOL_SIZE` + `WORKER_DB_MAX_OVERFLOW`, **default 2+2** — unchanged from when those numbers were hardcoded, so a host that sets nothing behaves exactly as before. The pool is never derived from `WORKER_CONCURRENCY`: across many worker processes, retained idle connections can exhaust shared Postgres even when no jobs are running.

That default is known to throttle, and it is a deliberate trade. Those 4 connections must cover `WORKER_CONCURRENCY` jobs *plus* the heartbeat loop, the credential limiter and the cost monitor, so a `WORKER_CONCURRENCY=4` host really runs 1–2 jobs; the rest fail `QueuePool limit of size 2 overflow 2 reached, connection timed out, timeout 30.00` (measured 2026-08-12 across 33 busy hosts: 17 ran 1 job, 14 ran 2, one 3, one 4). Those timeouts are **retryable**; the failure mode on the other side of the ceiling is not — a fleet-wide raise turns them into hard `too many clients` refusals that also lock the head out of its own database.

**Before raising either value**, get headroom first (pgbouncer in front of Postgres, or a larger `max_connections`) and do the fleet arithmetic:

```
connections ≈ hosts × processes-per-host × (pool_size + max_overflow)   <   max_connections
```

Processes-per-host is 2–3 here, not 1 — the head runs the API *and* the read-only viewer (`viewer_main`), and each process builds its own pool. Measured today: 38 hosts draw ~203 of `max_connections=250` at only ~57 concurrent jobs, i.e. there is no room to raise anything fleet-wide until that ceiling moves. Raise per host, and leave room for the head, the viewer and an operator `psql`.

*Sizing recommendation once pgbouncer is in front of Postgres* (not yet measured — recompute against the real fleet): for `WORKER_CONCURRENCY=4` hosts, `WORKER_DB_POOL_SIZE=6` (concurrency + heartbeat + one spare for the limiter/cost monitor) and `WORKER_DB_MAX_OVERFLOW=4` (absorbs the DAG wave bursts, then drains). That is 10 peak per process → 38 hosts × ~2.5 processes × 10 ≈ 950 *client* connections, which only works behind a transaction-mode pooler: size `max_client_conn` ≥ ~1200 and `default_pool_size` ~100–120 so real Postgres backends stay near 120 of 250. Straight at Postgres those same numbers are ~3.8× over the ceiling, which is why the default does not move.

**Worker-claimed-but-died jobs stay `running` until reclaim sweep.** The sweep keys on `RECLAIM_STALE_SECONDS` (default **120s**, `config.py`): a dead worker's `running` job is reclaimed to `pending` within ~2 minutes. Heartbeats keep live jobs' claims fresh, so a long-running job is never falsely reclaimed. There is no "2× job timeout" threshold.

**Draining a worker / taking a PC offline gracefully.** `POST /api/v1/workers/{pc_id}/drain` tells the worker to stop claiming new jobs and finish its in-flight work before quitting. The worker polls its own status on every registry heartbeat (`heartbeat_seconds`, default 30s); when it sees `draining` it calls `stop()` and does NOT write `"online"` back (which would clobber the signal). Use `POST /api/v1/workers/{pc_id}/undrain` to cancel. Once all in-flight jobs on that PC finish, the worker process exits cleanly.

**Head restart is now fleet-safe.** As of Cluster 5 / P1, the API startup orphan sweep is peer-aware: it resets-all `running` jobs to `pending` only when the `workers` table shows no live peers (single-host fast recovery, behavior unchanged); when live peers are present, only jobs whose lease is older than `RECLAIM_STALE_SECONDS` are reset. This means restarting the head/API pod no longer yanks a peer worker's freshly-heartbeated jobs.

**Operator-token changes are hard cuts.** Startup rejects the old `123` value
even when it appears beside a strong value. The API budget pause is not a global
claim fence (CLI jobs still pass), so install a temporary version floor above
the target version **and every reported/configured process version**, including
`WORKER_CODE_VERSION` overrides; an unreachable host without a proven bound
or readable startup target/environment aborts the rotation with no tombstone,
SHA, supervisor, or network exception. Bring it reachable or complete a
separately authorized decommission, then restart preflight. Drain/stop every
worker process—including post-done archive work—and require zero active jobs
and credential slots. Stage one token
everywhere with `WORKER_CONCURRENCY=0` on the head; the operator restarts the
head, then workers restart and publish the expected auth fingerprint while
still fenced. Automation must not kill/restart the user-owned head. Follow
[`docs/runbooks/operator-token-rotation.md`](./runbooks/operator-token-rotation.md),
including the owner-scoped floor restore/unpause or explicit foreign-pause
handoff and the six-key/Host-59 preservation checks. The final floor is
`max(prior,target)`, never blindly the old floor. Every known host remains
reachable with its startup target/environment/override verified and attested
through final reopen and rollback, or is separately decommissioned before the
operator restarts the full preflight. Rollback defaults to a sealed strong
token on current hardened code—an alternate build must be predesignated and
retain the complete auth/vault/fence hardening.

**SPA + auth.** If `AUTH_TOKEN` is set but the SPA's sessionStorage has no token, every page load redirects to `/login`. The login form takes a token; paste-and-submit. In production, the upstream service either (a) injects the bearer token via reverse proxy, or (b) hands the token to the SPA via postMessage / URL fragment / iframe init.

**Gemini cache columns are dead/legacy (no-op).** The `books.gemini_cache_*` columns are leftovers from the removed Gemini *file-cache* SDK era — nothing reads or writes them, there is no server-side cache anymore. They're kept nullable only for backwards-compat. No pod-lifetime concern. (Note: this is only about the legacy cache — `transport=api` *does* use SDKs today, `google-genai` + `anthropic` via `app/services/api_transport.py`.)

**Idempotency-Key in-memory cache** is per-process. With multi-pod API, the same Idempotency-Key sent to two pods will create two jobs (the natural-key + advisory lock still prevent same-section duplicates). For strict cross-pod idempotency, move `_IDEMPOTENCY_CACHE` from `app/api/v1/jobs.py` to a Redis or DB table. For most deployments, the natural-key idempotency is sufficient.


**Versioned homework regeneration ships dormant — and the UI flag is build-time.** The
feature is gated by `REGENERATION_ENABLED` + `REGENERATION_PUBLISHER_ENABLED` (both
`false`) **and** a third, separate switch on the frontend: the SPA must be built with
`VITE_REGENERATION_ENABLED=1`. `isRegenerationEnabled` matches the **literal string
`"1"`** — `true`, `TRUE`, `yes` and `on` all silently leave the nav item and the
`/regeneration` route out of the bundle, with no warning anywhere, so an operator who
sets `=true` will report "the feature did not ship". Because it is baked in at build
time, changing it requires `npm run build` and a reload; editing `.env` and restarting
the API does nothing for the frontend. Note also that the shipped `Dockerfile` runs a
plain `npm run build` with **no `ARG`/`ENV` for this variable**, so an image built
as-is always ships the regeneration UI hidden — on a bare-metal head, build the SPA on
the host (`cd web && VITE_REGENERATION_ENABLED=1 npm run build`; FastAPI serves
`web/dist`).

**Regeneration needs the image to know its own commit.** A campaign is an audit record, so
`POST /regeneration/campaigns` refuses with a structured `409 app_git_revision_unavailable`
rather than store unknown provenance. The resolution order is: explicit `app_git_revision` in
the request → the `APP_GIT_REVISION` environment variable → `code_version.GIT_SHA` from a real
checkout → the refusal. A container has **no `.git` and no git binary**, so inside one only the
first two can answer. CI already handles this — `.github/workflows/docker-publish.yml` passes
`APP_GIT_REVISION=${{ github.sha }}` as a build arg and the `Dockerfile` re-exports it as a
runtime `ENV`. But an image built by hand without `--build-arg`, or one built before this
existed, ships an empty value and cannot create campaigns at all. **No flag turns this on;** the
fixes are a rebuild with the build arg, `APP_GIT_REVISION=<sha>` in the running container's
environment, running from a git checkout, or sending the field on the request. On a bare-metal
head the checkout answers by itself — and a stale `APP_GIT_REVISION` copied into a shared `.env`
**overrides** it and silently mis-stamps every campaign, permanently.

**Each output language needs its own Notion subject mapping before a campaign spends.** The
pre-spend preflight requires every target's `{lang}:{subject}|{grade}` destination to resolve,
per language — a populated `toc_entries.notion_lesson_page_id` does **not** substitute for one.
That column is language-blind (whichever lineage archived first owns it), so the publisher treats
it as a hint and only honours it once a child listing proves it belongs to that language's own
`Generated Homeworks` container; every delivery is otherwise resolved beneath the target's own
language subject page. Configure the `ru`/`en` roots before launching a campaign in those
languages, or the launch is blocked with an actionable list and nothing is spent.

**Regeneration and the cost caps — both scopes.** Revision jobs carry `batch_id=NULL`
(enforced by CHECK), and the per-batch cap joins usage rows to a batch, so
`COST_CAP_BATCH_USD` **never applies to a regeneration campaign**, however large. The
fleet-daily query has no job-kind filter, so regeneration spend **does** count toward
`COST_CAP_FLEET_DAILY_USD` and a big campaign can trip it and **pause ordinary api
batches fleet-wide**. Budget and stage campaigns accordingly.

**Regeneration history blocks book/TOC deletion.** A target records a publication
version consumed forever, so migration 0063's foreign keys are `RESTRICT` — with **one
intentional exception: `fk_regeneration_targets_source_job_id` is `SET NULL`**. The
restrictive half of the source-deletion rule is `fk_homework_jobs_revision_of_job_id`
(RESTRICT), which forces a child-first order: only once a revision job is deleted can
its source be, and that delete then blanks the target's `source_job_id` rather than
blocking. While history exists, book delete, TOC-entry delete and `/toc/retry` refuse
with a structured `409` (`book_delete_blocked_by_regeneration` /
`toc_entry_delete_blocked_by_regeneration` / `toc_retry_blocked_by_regeneration`) rather
than a raw FK error — those refusals match **any** target row for the lesson, so the
`SET NULL` exception does not weaken them. No child-first purge tool ships in this
release. Cancelling or abandoning a campaign does not erase the audit history and does
not unblock them.

**Rolling regeneration back** is just: turn both backend flags off and restart the
head. No publisher loop starts, every route 404s, in-flight revision jobs still finish
as ordinary jobs and are never archived to the legacy `Homework` page
(`notion_archive.archive_job` intrinsically refuses any job with
`revision_of_job_id IS NOT NULL`, regardless of `force` or caller), and **every
existing `Homework` / `Homework V2` / `Homework V3` page in Notion is untouched** —
rollback deletes nothing.

**It does, however, strand any lesson caught mid-campaign.**
`uq_regeneration_targets_active_lineage` allows one **non-terminal** target per
`(toc_entry_id, output_language)`, and only `published`/`abandoned` are terminal — so a
target left in any other status keeps holding its lineage, while all four routes that would
clear it (`retry-generation`, `retry-publication`, `abandon`, and the campaign-level `cancel`,
which converges every non-terminal target of its campaign) 404 with the feature off.
No new campaign can touch those lessons until the flags are restored and an operator
drives each target to `published` or `abandoned`. That is a uniqueness fence, not data
loss or a Notion change. If a re-launch is expected soon, drain or abandon in-flight
targets **before** flipping the flags off.


## Dashboard viewer port (worklog 0153)

`uv run uvicorn viewer_main:app --host 0.0.0.0 --port 8001` — a separate read-only process (only `/health` + the coverage GET; no worker, no mutations). Requires `DASHBOARD_TOKEN` in the same `.env` (startup refuses when empty or overlapping `AUTH_TOKEN` — the overlap check reads the local env, so if the viewer ever runs on a different host from the operator app, keep the token sets disjoint by convention) and a viewer FE build (`cd web && npm run build:viewer` → gitignored `web/dist-viewer`).
