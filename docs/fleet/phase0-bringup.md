# Fleet Phase 0 bring-up

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
Run the smoke (scripts/fleet_contention_smoke.py, added in Task 3) to prove
contention-safe claiming across PCs.
