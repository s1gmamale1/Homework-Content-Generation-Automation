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
# edit .env: set GEMINI_API_KEY=...
docker compose up --build
```

`http://localhost:8000` runs API + SPA + embedded worker. Postgres is in-compose with a persistent volume. Migrations run automatically before the API starts.

---

## Required environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://...` (asyncpg driver, not psycopg) |
| `GEMINI_API_KEY` | yes | — | https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | no | `gemini-2.5-flash` | Pin to a stable release; avoid `*-preview` / `*-exp` in prod |
| `AUTH_TOKEN` | **strongly recommended** | empty | Empty disables auth (every request `user="anonymous"`). Set this. |
| `WORKER_CONCURRENCY` | no | `4` | Embedded worker job concurrency. Set `0` in API-only pods. |
| `JOB_TIMEOUT_SECONDS` | no | `600` | Hard ceiling per job. |
| `QUEUE_MAX_ATTEMPTS` | no | `3` | Retries before terminal failure. |
| `QUEUE_BACKPRESSURE_LIMIT` | no | `50` | Queue depth → 503. `0` disables. |
| `GEMINI_MAX_CONCURRENCY` | no | `8` | Process-wide cap on Gemini calls. Tune to your RPM tier. |
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
    image: class-homework-builder:latest
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      WORKER_CONCURRENCY: "4"   # embedded worker, 4 concurrent jobs
      # ... other env vars
```

This is what `docker compose up` runs by default.

## Topology 2: separate API + worker pods (horizontally scaled)

**Best for:** higher throughput, independent scaling, resilient deploys (rolling-restart API without dropping jobs).

```yaml
services:
  api:                     # N replicas
    image: class-homework-builder:latest
    command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      WORKER_CONCURRENCY: "0"   # disable embedded worker
      # ... other env vars

  worker:                  # M replicas (scale independently of API)
    image: class-homework-builder:latest
    command: ["python", "-m", "app.services.worker"]
    environment:
      WORKER_CONCURRENCY: "4"
      # ... other env vars (no AUTH_TOKEN needed; worker doesn't serve HTTP)
```

To exercise this locally:
```bash
docker compose --profile scaled up
# add `WORKER_CONCURRENCY=0` to .env to disable the embedded worker
```

---

## Migrations

Every deploy must run `alembic upgrade head` before the API starts. Three options, in order of preference:

**Option A — `migrate` service in compose (already wired):**
```yaml
services:
  migrate:
    image: class-homework-builder:latest
    command: ["alembic", "upgrade", "head"]
    restart: "no"
  api:
    depends_on:
      migrate:
        condition: service_completed_successfully
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

## CI: build + push to Docker Hub

Workflow at `.github/workflows/docker-publish.yml`. Builds the image on every
push to `main`, on every `v*.*.*` tag, and on PRs (build-only — no push).
Multi-arch (amd64 + arm64), caches via GitHub Actions cache, attaches SBOM
and provenance.

### One-time setup

Two secrets + one optional variable on the repo:

1. **`DOCKERHUB_USERNAME`** (secret): your Docker Hub username
2. **`DOCKERHUB_TOKEN`** (secret): an access token from
   https://hub.docker.com/settings/security — use a **read+write+delete**
   token scoped to a single repo, not your account password.
3. **`IMAGE_NAME`** (variable, optional): full image path, e.g. `myorg/class-homework-builder`.
   If unset, defaults to `<DOCKERHUB_USERNAME>/<repo-name>`.

To add them: GitHub repo → Settings → Secrets and variables → Actions:
- "New repository secret" → DOCKERHUB_USERNAME
- "New repository secret" → DOCKERHUB_TOKEN
- "Variables" tab → IMAGE_NAME (optional)

### Tag scheme

| Trigger | Tags pushed |
|---|---|
| `push` to `main` | `latest`, `main`, `main-<short-sha>` |
| `push` tag `v1.2.3` | `1.2.3`, `1.2`, `1`, `v1.2.3`, `latest` |
| `workflow_dispatch` (manual) | `manual-<sha>` |
| `pull_request` | (build only — verifies Dockerfile, no push) |

### Releasing a versioned build

```bash
git tag v0.1.0
git push origin v0.1.0
# CI builds, pushes 1, 1.0, 1.0.0, v0.1.0, latest
```

### Pulling the image into a deploy

```bash
docker pull docker.io/myorg/class-homework-builder:latest
# or pin a digest for production:
docker pull docker.io/myorg/class-homework-builder@sha256:...
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
  app = "uvicorn main:app --host 0.0.0.0 --port 8080"
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
- [ ] `GEMINI_MODEL` pinned to a stable release (no `-preview` / `-exp`)
- [ ] `ALLOW_ORIGINS` set to actual frontend origin (not `*`) if API and SPA are on different domains
- [ ] Postgres has automated backups enabled (managed services usually do this; self-hosted needs `pg_dump` cron)
- [ ] Healthcheck endpoint `/health` reachable from your platform's liveness probe
- [ ] Migrations applied: `alembic upgrade head`
- [ ] Worker concurrency tuned to your tier:
      `GEMINI_MAX_CONCURRENCY ≤ your_RPM_tier / 4` (each job makes ~3-4 phase calls in parallel)
- [ ] If horizontally scaling: `WORKER_CONCURRENCY=0` on API pods so they don't double-claim jobs alongside dedicated worker pods

---

## Observability

The codebase emits structured loguru logs to stdout. In production, route them to your aggregator (Datadog, Loggly, CloudWatch, etc.). Key signals:

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
- `gemini_calls_total` from `gemini_usages` table
- `gemini_fresh_tokens_total` (sum of `prompt_token_count - cached_content_token_count`)

These aren't wired yet but the data is in the DB / logs — easy to add a `/metrics` endpoint when needed.

---

## Operational gotchas

**Connection pool sizing.** Each worker holds 2-4 DB connections during a job. With `WORKER_CONCURRENCY=4` and 2 worker pods, peak is ~32 connections. Default pool is 20+30=50 connections per process. If you scale workers to >5 pods on shared Postgres (Neon free tier = 100 connections), you'll exhaust. Either: bump `pool_size` in `app/db.py`, or scale Postgres.

**Worker-claimed-but-died jobs stay `running` until reclaim sweep.** Default sweep interval is 60s with a stale-after threshold of 2× the job timeout (i.e., 1200s by default). A job can sit stuck for up to ~20 min before being reclaimed. Tune `JOB_TIMEOUT_SECONDS` to make this faster if needed.

**SPA + auth.** If `AUTH_TOKEN` is set but the SPA's sessionStorage has no token, every page load redirects to `/login`. The login form takes a token; paste-and-submit. In production, the upstream service either (a) injects the bearer token via reverse proxy, or (b) hands the token to the SPA via postMessage / URL fragment / iframe init.

**Cache TTLs vs pod lifetime.** The per-book Gemini cache (`books.gemini_cache_name`) has a 6h server-side TTL but is referenced by name. If a pod dies, a new pod with the same DB row reuses the cache fine — no data loss.

**Idempotency-Key in-memory cache** is per-process. With multi-pod API, the same Idempotency-Key sent to two pods will create two jobs (the natural-key + advisory lock still prevent same-section duplicates). For strict cross-pod idempotency, move `_IDEMPOTENCY_CACHE` from `app/api/v1/jobs.py` to a Redis or DB table. For most deployments, the natural-key idempotency is sufficient.
