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
# edit .env: set AUTH_TOKEN=... (and the transport=api keys only if you use api jobs)
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
| `GEMINI_API_KEY` | no | — | Only for `transport=api` gemini jobs (or use the Vertex SA pair `GOOGLE_APPLICATION_CREDENTIALS`+`GOOGLE_CLOUD_PROJECT`). Default `cli` jobs need no key. Read from `os.environ` by the api transport — the same-named config field is vestigial. |
| `ANTHROPIC_API_KEY` | no | — | For `transport=api` claude jobs. The worker computes **per-role** api-readiness flags at startup (`worker._compute_capabilities`: `can_claude_api`, `can_gemini_api`, `judge_api_ok`, `judge_fallback_api_ok`, `extract_api_ok`) and `claim_next_job` ANDs each job's *resolved* per-role transports against them — so a worker can claim *some* api jobs without *all* creds (e.g. an api-content gemini job with cli judge+extract needs no `ANTHROPIC_API_KEY`). |
| `GEMINI_MODEL` | no | `gemini-2.0-flash-exp` | Vestigial (unread by the runtime). The *extract pin* `EXTRACT_MODEL` is separately `gemini-2.5-flash`. |
| `AUTH_TOKEN` | **strongly recommended** | `"123"` | Code default is the literal token `"123"` (`config.py`); `.env.example` ships `AUTH_TOKEN=` (empty) which **disables** auth (every request `user="anonymous"`). ⚠️ A bare-metal run with no `.env` entry gets `"123"` — auth silently ON with a guessable token. Set this to a strong value. |
| `WORKER_CONCURRENCY` | no | `4` | Embedded worker job concurrency. Set `0` in API-only pods. |
| `COST_CAP_BATCH_USD` | no | `0` | Per-batch API spend cap in USD. `0` disables the check. When a batch's api spend exceeds this, the budget monitor pauses it (`batches.paused_at`/`paused_reason`) so no further api jobs from that batch are claimed. The pause reason is set to `"batch-cap"`. |
| `COST_CAP_FLEET_DAILY_USD` | no | `0` | Fleet-wide rolling 24h api spend cap in USD. `0` disables. When the 24h api spend across all jobs exceeds this, the budget monitor sets the `budget_state` singleton's `api_paused_at`, blocking all api-transport jobs across the entire fleet. The pause reason is set to `"fleet-daily-cap"`. |
| `COST_CHECK_INTERVAL_SECONDS` | no | `60` | How often (seconds) the budget monitor loop runs its spend checks. Lower values catch overruns sooner; at `60` the worst-case overrun is roughly one job's api cost above the cap. |
| `JOB_TIMEOUT_SECONDS` | no | `1800` | Hard ceiling per job. (`.env.example` overrides it to `600`.) |
| `QUEUE_MAX_ATTEMPTS` | no | `3` | Retries before terminal failure. |
| `QUEUE_BACKPRESSURE_LIMIT` | no | `50` | Queue depth → 503. `0` disables. |
| `AGENT_MAX_CONCURRENCY` | no | `8` | ⚠️ **The live knob** — process-wide cap on *all* CLI subprocesses. `agent._effective_concurrency()` reads this first; when it is left at its default (8), `GEMINI_MAX_CONCURRENCY` is used as a fallback so existing configs are not broken. Tune to your RPM tier (`AGENT_MAX_CONCURRENCY ≤ your_RPM_tier / 4`). **Per-process** (module global), so the real fleet cap is `N_processes × this`, not deployment-wide. |
| `GEMINI_MAX_CONCURRENCY` | no | `8` | ⚠️ **Deprecated fallback** — honoured only when `AGENT_MAX_CONCURRENCY` is left at its default (8). Kept for backwards-compat with existing `.env` files. Prefer `AGENT_MAX_CONCURRENCY` for new deployments. |
| `RATE_LIMIT_MAX_RETRIES` | no | `4` | Reactive 429 backoff (`concurrency-knob-1` ph1): how many times `agent._spawn` retries a call that came back with a transient rate-limit (`429` / `RESOURCE_EXHAUSTED` / `overloaded_error` / "too many requests"; **not** auth `401/403` or `MAX_TOKENS`) before returning the failure to the phase-level failover. `0` disables the in-place retry. |
| `RATE_LIMIT_BASE_DELAY_SECONDS` | no | `2.0` | First backoff delay; grows exponentially per attempt (with jitter). The backoff `asyncio.sleep` holds **no** concurrency slot. |
| `RATE_LIMIT_MAX_DELAY_SECONDS` | no | `30.0` | Ceiling on the exponential backoff delay. |
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

- [ ] `AUTH_TOKEN` set to a strong random value (or comma-separated list for multiple services)
- [ ] `ENABLE_DOCS=false`
- [ ] `EXTRACT_MODEL` / `EXTRACT_PROVIDER` left at defaults (`gemini-2.5-flash` / `gemini`) or pinned to a stable model; `JUDGE_MODEL` likewise (`GEMINI_MODEL` is vestigial — nothing reads it)
- [ ] `ALLOW_ORIGINS` set to actual frontend origin (not `*`) if API and SPA are on different domains
- [ ] Postgres has automated backups enabled (managed services usually do this; self-hosted needs `pg_dump` cron)
- [ ] Healthcheck endpoint `/health` reachable from your platform's liveness probe
- [ ] Migrations applied: `alembic upgrade head`
- [ ] Worker concurrency tuned to your tier:
      `AGENT_MAX_CONCURRENCY ≤ your_RPM_tier / 4` (the live knob — `agent._effective_concurrency()`; each job makes ~3-4 phase calls in parallel; `GEMINI_MAX_CONCURRENCY` is the deprecated fallback used only when `AGENT_MAX_CONCURRENCY` is left at default)
- [ ] If horizontally scaling: `WORKER_CONCURRENCY=0` on API pods so they don't double-claim jobs alongside dedicated worker pods

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

**Connection pool sizing.** Each worker holds 2-4 DB connections during a job. With `WORKER_CONCURRENCY=4` and 2 worker pods, peak is ~32 connections. Default pool is 20+30=50 connections per process. If you scale workers to >5 pods on shared Postgres (Neon free tier = 100 connections), you'll exhaust. Either: bump `pool_size` in `app/db.py`, or scale Postgres.

**Worker-claimed-but-died jobs stay `running` until reclaim sweep.** The sweep keys on `RECLAIM_STALE_SECONDS` (default **120s**, `config.py`): a dead worker's `running` job is reclaimed to `pending` within ~2 minutes. Heartbeats keep live jobs' claims fresh, so a long-running job is never falsely reclaimed. There is no "2× job timeout" threshold.

**Draining a worker / taking a PC offline gracefully.** `POST /api/v1/workers/{pc_id}/drain` tells the worker to stop claiming new jobs and finish its in-flight work before quitting. The worker polls its own status on every registry heartbeat (`heartbeat_seconds`, default 30s); when it sees `draining` it calls `stop()` and does NOT write `"online"` back (which would clobber the signal). Use `POST /api/v1/workers/{pc_id}/undrain` to cancel. Once all in-flight jobs on that PC finish, the worker process exits cleanly.

**Head restart is now fleet-safe.** As of Cluster 5 / P1, the API startup orphan sweep is peer-aware: it resets-all `running` jobs to `pending` only when the `workers` table shows no live peers (single-host fast recovery, behavior unchanged); when live peers are present, only jobs whose lease is older than `RECLAIM_STALE_SECONDS` are reset. This means restarting the head/API pod no longer yanks a peer worker's freshly-heartbeated jobs.

**SPA + auth.** If `AUTH_TOKEN` is set but the SPA's sessionStorage has no token, every page load redirects to `/login`. The login form takes a token; paste-and-submit. In production, the upstream service either (a) injects the bearer token via reverse proxy, or (b) hands the token to the SPA via postMessage / URL fragment / iframe init.

**Gemini cache columns are dead/legacy (no-op).** The `books.gemini_cache_*` columns are leftovers from the removed Gemini *file-cache* SDK era — nothing reads or writes them, there is no server-side cache anymore. They're kept nullable only for backwards-compat. No pod-lifetime concern. (Note: this is only about the legacy cache — `transport=api` *does* use SDKs today, `google-genai` + `anthropic` via `app/services/api_transport.py`.)

**Idempotency-Key in-memory cache** is per-process. With multi-pod API, the same Idempotency-Key sent to two pods will create two jobs (the natural-key + advisory lock still prevent same-section duplicates). For strict cross-pod idempotency, move `_IDEMPOTENCY_CACHE` from `app/api/v1/jobs.py` to a Redis or DB table. For most deployments, the natural-key idempotency is sufficient.
