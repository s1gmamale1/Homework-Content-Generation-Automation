# Fleet Phase 0 — One DB + Dockerized Worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that multiple standalone worker containers — including one on a *separate PC* — pull jobs from one shared Postgres "head" with contention-safe claiming, and capture it as a repeatable proof.

**Architecture:** Reuse the existing Docker image + standalone worker (`python -m app.services.worker`) + the `FOR UPDATE SKIP LOCKED` claim (`claim_next_job`). Add only what's genuinely missing for *multi-PC*: a worker-only compose pointed at a remote head, a LAN-reachable head, an automated contention test, and a container smoke. The API runs with `WORKER_CONCURRENCY=0`; workers run as separate containers.

**Tech Stack:** Docker / docker-compose, Postgres 16, SQLAlchemy async + asyncpg, pytest + pytest-asyncio, Alembic.

---

## ⚠ Reality note — most of Phase 0 already exists (do NOT rebuild)

Verified against the code on branch `Nggaev-v2`:
- **Dockerfile** — multi-stage, builds one image that runs API *or* worker (`Dockerfile:1-55`).
- **`docker-entrypoint.sh`** — runs `alembic upgrade head` when `RUN_MIGRATIONS!=0`, then `exec "$@"` (so `python -m app.services.worker` runs cleanly) (`docker-entrypoint.sh:1-17`).
- **`docker-compose.yml` `worker` service** — `command: python -m app.services.worker`, `profiles: [scaled]`, `replicas: 2`, `RUN_MIGRATIONS=0`, same central `DATABASE_URL` (`docker-compose.yml:51-71`).
- **Embedded-worker gate** — `if settings.worker_concurrency > 0` (`main.py:62`); `WORKER_CONCURRENCY=0` disables it.
- **Standalone entrypoint** — `run_standalone` / `__main__` (`app/services/worker.py:341/366`).
- **Contention-safe claim** — `claim_next_job` uses `.with_for_update(skip_locked=True)` (`app/repositories/jobs.py:204-249`).

**Therefore this plan does NOT recreate the Dockerfile, the compose worker service, the entrypoint, or the claim.** It adds the multi-PC artifact + the missing proofs. The genuinely-new gaps: (1) the central DB in compose is **not reachable from other PCs** (`postgres` is on an internal `local-network`, unpublished); (2) there is **no worker-only compose** for a remote PC; (3) **no automated test** exercises `claim_next_job` against a real DB (the unit suite is deliberately DB-free — `tests/conftest.py:1-33`).

---

## File Structure

- `tests/integration/__init__.py` — new package marker.
- `tests/integration/test_claim_contention.py` — new: real-DB proof that two concurrent claims never grab the same row. Skipped unless `RUN_DB_INTEGRATION=1` (keeps the default DB-free suite untouched).
- `docker-compose.worker.yml` — new: worker-only stack a remote PC runs against a head `DATABASE_URL` (no local postgres).
- `docs/fleet/phase0-bringup.md` — new: the exact head-reachability + remote-worker + smoke recipe (one runbook, no guesswork).
- `scripts/fleet_contention_smoke.py` — new: seeds 2 jobs into the head and asserts both get claimed by live workers (container-level proof).
- `docs/memory/MASTER_MEMORY.md` + `docs/memory/INDEX.md` — append the Phase 0 worklog.

---

### Task 1: Automated contention proof against a real DB

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_claim_contention.py`

This is the one genuinely test-driven piece: prove `claim_next_job`'s `FOR UPDATE SKIP LOCKED` means two concurrent sessions never claim the same job. It needs a real Postgres, so it is **guarded** (skipped unless `RUN_DB_INTEGRATION=1`) and uses the real `SessionLocal` (which reads `DATABASE_URL`).

- [ ] **Step 1: Create the package marker**

```python
# tests/integration/__init__.py
```
(empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/integration/test_claim_contention.py
"""Real-DB proof: FOR UPDATE SKIP LOCKED prevents two workers claiming one job.

Skipped unless RUN_DB_INTEGRATION=1 AND a real DATABASE_URL points at a
throwaway Postgres (the default unit suite is DB-free — tests/conftest.py).

Run:
  docker run -d --name fleet-pg -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
    -e POSTGRES_DB=edu_homework -p 5433:5432 postgres:16-alpine
  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    alembic upgrade head
  RUN_DB_INTEGRATION=1 \
    DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    .venv/Scripts/python.exe -m pytest tests/integration/test_claim_contention.py -v
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import delete

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


@pytest.mark.asyncio
async def test_two_concurrent_claims_never_collide():
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    # ── seed: one book, one section, two pending jobs (committed) ──
    async with SessionLocal() as s:
        book = Book(
            subject="math-algebra",
            original_filename="contention-test.pdf",
            content_sha256="0" * 64,
            file_size_bytes=1,
            status="ready",
        )
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        book_id = book.id

    try:
        # ── two sessions hold their claims open simultaneously ──
        async with SessionLocal() as sa, SessionLocal() as sb:
            job_a = await jobs_repo.claim_next_job(sa, worker_id="A", max_attempts=3)
            # sa's row is locked-but-uncommitted; sb must SKIP it and take the other
            job_b = await jobs_repo.claim_next_job(sb, worker_id="B", max_attempts=3)
            assert job_a is not None, "worker A claimed nothing"
            assert job_b is not None, "worker B claimed nothing"
            assert job_a.id != job_b.id, "two workers claimed the SAME job"
            await sa.commit()
            await sb.commit()

        # ── no pending jobs left → a third claim returns None ──
        async with SessionLocal() as sc:
            assert await jobs_repo.claim_next_job(sc, worker_id="C", max_attempts=3) is None
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()
```

- [ ] **Step 3: Run it WITHOUT a DB to confirm it skips (default suite stays green)**

Run: `.venv/Scripts/python.exe -m pytest tests/integration/test_claim_contention.py -v`
Expected: `1 skipped` (RUN_DB_INTEGRATION not set).

- [ ] **Step 4: Run it WITH a throwaway DB to confirm it passes**

```bash
docker run -d --name fleet-pg -e POSTGRES_USER=edu -e POSTGRES_PASSWORD=edu \
  -e POSTGRES_DB=edu_homework -p 5433:5432 postgres:16-alpine
# wait ~3s for pg to accept connections, then:
```
Run (PowerShell, one line):
`$env:DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5433/edu_homework"; .venv\Scripts\python.exe -m alembic upgrade head; $env:RUN_DB_INTEGRATION="1"; .venv\Scripts\python.exe -m pytest tests/integration/test_claim_contention.py -v`
Expected: `1 passed`. (Teardown: `docker rm -f fleet-pg`.)

- [ ] **Step 5: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_claim_contention.py
git commit -m "test(fleet): real-DB proof claim_next_job is contention-safe (Phase 0)"
```

---

### Task 2: Worker-only compose for a remote PC + reachable head

**Files:**
- Create: `docker-compose.worker.yml`
- Create: `docs/fleet/phase0-bringup.md`

The existing `scaled` profile proves multi-worker on **one host**. A remote PC needs a worker-only stack (no local postgres) pointed at the head's `DATABASE_URL`, and the head's Postgres must be LAN-reachable.

- [ ] **Step 1: Create the worker-only compose**

```yaml
# docker-compose.worker.yml
# Worker-only stack for a REMOTE fleet PC. No local Postgres — points at the
# central head. Bring up on each worker PC with:
#   DATABASE_URL=postgresql+asyncpg://edu:edu@<HEAD_IP>:5432/edu_homework \
#     docker compose -f docker-compose.worker.yml up -d
services:
  worker:
    image: ${IMAGE:-ghcr.io/ganiyevuz/class-homework-builder:latest}
    pull_policy: always
    command: [ "python", "-m", "app.services.worker" ]
    environment:
      # REQUIRED: point at the head. No default — fail fast if unset.
      DATABASE_URL: ${DATABASE_URL:?set to the head, e.g. postgresql+asyncpg://edu:edu@HEAD_IP:5432/edu_homework}
      WORKER_CONCURRENCY: ${WORKER_CONCURRENCY:-4}
      RUN_MIGRATIONS: "0"   # head owns migrations; workers never migrate
    restart: unless-stopped
    deploy:
      replicas: ${WORKER_REPLICAS:-2}
```

- [ ] **Step 2: Validate the compose file parses**

Run: `docker compose -f docker-compose.worker.yml config`
Expected: prints the resolved config with no error (it WILL error if `DATABASE_URL` is unset — that's the intended fail-fast; set a dummy `DATABASE_URL=x` to see it parse).

- [ ] **Step 3: Write the bring-up runbook**

```markdown
# docs/fleet/phase0-bringup.md — Fleet Phase 0 bring-up

## 1. Head (one machine)
Make the central Postgres reachable on the LAN. Either publish the port on the
existing stack:

    # docker-compose.head-ports.yml (override — keeps prod compose untouched)
    services:
      postgres:
        ports: [ "5432:5432" ]

    docker compose -f docker-compose.yml -f docker-compose.head-ports.yml up -d postgres
    docker compose -f docker-compose.yml up -d api   # api runs migrations on start

Set `WORKER_CONCURRENCY=0` in the head's `.env` so the API does NOT also run an
embedded worker (workers live on the fleet PCs).

> Phase 0 uses a published Postgres port on a trusted LAN. PgBouncer + a hardened
> head are a later phase (spec §9.1 / §8).

## 2. Each worker PC
    export DATABASE_URL=postgresql+asyncpg://edu:edu@<HEAD_IP>:5432/edu_homework
    docker compose -f docker-compose.worker.yml up -d
    docker compose -f docker-compose.worker.yml logs -f   # expect "standalone worker bootstrapping"

## 3. Confirm the PC registered & polls
On the head: each worker logs a claim attempt every WORKER_POLL_INTERVAL (2s).
Run the smoke in Task 3 to prove contention-safe claiming across PCs.
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.worker.yml docs/fleet/phase0-bringup.md
git commit -m "feat(fleet): worker-only compose + Phase 0 bring-up runbook"
```

---

### Task 3: Container-level fleet smoke (two workers, one DB)

**Files:**
- Create: `scripts/fleet_contention_smoke.py`

A runnable proof that real worker containers **live-pull** from one DB. The image has no LLM CLIs, so claimed jobs **fail fast** — and the failure path (`mark_failed_with_retry`, `jobs.py:324/340`) **clears `claimed_by`**. So the smoke must NOT assert on `claimed_by` (transient); it asserts on **`attempts > 0`** — set on claim (`jobs.py:243`), **never reset by the failure path** — the durable proof that every job was pulled by a worker. Multi-worker is shown best-effort by live-sampling `claimed_by` during the run (informational only). The deterministic per-row contention guarantee is **Task 1**.

- [ ] **Step 1: Write the smoke script**

```python
# scripts/fleet_contention_smoke.py
"""Phase 0 fleet smoke: seed N jobs into the head and prove live worker
containers pulled them off the one shared DB.

Asserts attempts>0 for every job (set on claim at jobs.py:243; the failure path
clears claimed_by but NOT attempts), so it stays valid even though the CLI-less
image fails each job right after claiming it. Multi-worker is sampled live
(best-effort) because claimed_by is cleared on failure. The deterministic
contention proof is Task 1. Run AFTER bringing up postgres + >=2 workers.

  DATABASE_URL=postgresql+asyncpg://edu:edu@localhost:5433/edu_homework \
    .venv/Scripts/python.exe scripts/fleet_contention_smoke.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.toc_entry import TOCEntry
from app.repositories import jobs as jobs_repo

N = 4
SAMPLE_SECONDS = 25


async def _seed():
    async with SessionLocal() as s:
        book = Book(subject="math-algebra", original_filename="smoke.pdf",
                    content_sha256="1" * 64, file_size_bytes=1, status="ready")
        s.add(book)
        await s.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        s.add(toc)
        await s.flush()
        for _ in range(N):
            await jobs_repo.create(s, book_id=book.id, toc_entry_id=toc.id, subject="math-algebra")
        await s.commit()
        return book.id


async def main() -> None:
    book_id = await _seed()
    print(f"seeded {N} jobs; sampling up to {SAMPLE_SECONDS}s while workers pull...")
    workers_seen: set[str] = set()
    try:
        # Live-sample claimed_by (transient) and watch attempts climb.
        for _ in range(SAMPLE_SECONDS):
            await asyncio.sleep(1)
            async with SessionLocal() as s:
                rows = (await s.execute(
                    select(HomeworkJob.claimed_by, HomeworkJob.attempts)
                    .where(HomeworkJob.book_id == book_id)
                )).all()
            for r in rows:
                if r.claimed_by:
                    workers_seen.add(r.claimed_by)
            if all((r.attempts or 0) > 0 for r in rows):
                break  # every job has been claimed at least once

        async with SessionLocal() as s:
            final = (await s.execute(
                select(HomeworkJob.attempts, HomeworkJob.status)
                .where(HomeworkJob.book_id == book_id)
            )).all()
        pulled = [r for r in final if (r.attempts or 0) > 0]
        print(f"pulled {len(pulled)}/{N} jobs (attempts>0); "
              f"workers sampled: {sorted(workers_seen) or '<none captured>'}; "
              f"statuses: {sorted(r.status for r in final)}")
        assert len(pulled) == N, f"only {len(pulled)}/{N} jobs were pulled by a worker"
        print("PASS: all jobs pulled by live worker container(s) off one DB")
        if len(workers_seen) >= 2:
            print(f"BONUS: observed {len(workers_seen)} distinct workers live")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id == book_id))
            await s.execute(delete(TOCEntry).where(TOCEntry.book_id == book_id))
            await s.execute(delete(Book).where(Book.id == book_id))
            await s.commit()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the smoke against the scaled stack**

```bash
# one host, full stack with 2 workers + WORKER_CONCURRENCY=0 on api:
WORKER_CONCURRENCY=0 docker compose --profile scaled up -d --build
docker run -d --name fleet-pg ...   # OR use the compose postgres; publish 5432 first
```
Run (PowerShell, one line, against the head DB):
`$env:DATABASE_URL="postgresql+asyncpg://edu:edu@localhost:5432/edu_homework"; .venv\Scripts\python.exe scripts\fleet_contention_smoke.py`
Expected: `PASS: all jobs pulled by live worker container(s) off one DB` (plus a `BONUS: observed N distinct workers live` line when ≥2 workers are sampled).

- [ ] **Step 3: Commit**

```bash
git add scripts/fleet_contention_smoke.py
git commit -m "feat(fleet): Phase 0 container smoke — 2 workers claim from one DB"
```

---

### Task 4: Worklog

**Files:**
- Modify: `docs/memory/MASTER_MEMORY.md` (append a new worklog entry at the top of the worklog list)
- Modify: `docs/memory/INDEX.md` (append a row)

- [ ] **Step 1: Append the worklog entry to `docs/memory/MASTER_MEMORY.md`**

```markdown
## [00XX] Fleet Phase 0 — one DB + Dockerized worker (2026-06-06)

**What:** Proved the existing worker image + central-DB wiring runs as a multi-PC
fleet. Added `docker-compose.worker.yml` (worker-only stack for a remote PC →
head `DATABASE_URL`), `docs/fleet/phase0-bringup.md` (head-reachability + bring-up
runbook), a guarded real-DB test (`tests/integration/test_claim_contention.py`)
proving `claim_next_job`'s FOR UPDATE SKIP LOCKED never double-claims, and
`scripts/fleet_contention_smoke.py` (container-level proof).

**Reused, not rebuilt:** Dockerfile, `worker` compose service (scaled profile),
`docker-entrypoint.sh` RUN_MIGRATIONS switch, `main.py:62` WORKER_CONCURRENCY=0
gate, `claim_next_job` — all pre-existing.

**Proof:** integration test PASS against throwaway pg; smoke PASS — all seeded
jobs pulled (attempts>0) by live worker containers off one DB (claimed_by is
cleared by the CLI-less failure path, so the smoke asserts on attempts).

**Next:** Phase 1 — `workers` registry table + head-side liveness view (reclaim
already shipped in 0031).
```
(Replace `00XX` with the next worklog number — check the current highest in the file.)

- [ ] **Step 2: Append the INDEX row to `docs/memory/INDEX.md`**

```markdown
| 00XX | 2026-06-06 | Fleet Phase 0: one DB + Dockerized worker (compose.worker + contention proof) |
```
(Match the worklog number; match the existing column format in the file.)

- [ ] **Step 3: Commit**

```bash
git add docs/memory/MASTER_MEMORY.md docs/memory/INDEX.md
git commit -m "docs(memory): worklog — fleet Phase 0 (one DB + Dockerized worker)"
```

---

## Self-Review

**1. Spec coverage (Phase 0 acceptance — spec §6/§10):** "make Postgres the network-reachable head" → Task 2 (head-ports override + runbook). "package the standalone worker as a Docker image reading one DB_URL" → already exists; Task 2 worker-only compose points it at the head. "run API with worker_concurrency=0" → runbook Step 1 + smoke Step 2. "prove two worker containers pull from one DB with no contention" → Task 1 (deterministic) + Task 3 (container-level). Worklog → Task 4. ✓ No gaps.

**2. Placeholder scan:** No TBD/TODO. Every code step shows full file content. The only intentional fill-in is the worklog number `00XX` (Task 4), which can't be known until execution (depends on the current highest worklog) — flagged explicitly with how to resolve.

**3. Type/identifier consistency:** `jobs_repo.create(session, *, book_id, toc_entry_id, subject, ...)`, `claim_next_job(session, *, worker_id, max_attempts)`, `Book(subject, original_filename, content_sha256, file_size_bytes, status)`, `TOCEntry(book_id, section_title, order_index)` — all match the real signatures verified in `app/repositories/jobs.py` and `app/models/*.py`. `RUN_DB_INTEGRATION`, `DATABASE_URL`, `WORKER_CONCURRENCY` env names match `app/config.py` / `docker-entrypoint.sh` / `docker-compose.yml`.

**Honesty caveat:** Tasks 2 and 3 are ops verification (Docker bring-up + smoke), not unit-TDD — appropriate because Phase 0 is packaging an already-tested code path across machines. Task 1 is the genuinely test-driven piece.
