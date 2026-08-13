from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

def _pool_config(*, worker_concurrency: int) -> dict[str, int]:
    """Return role-appropriate pool bounds.

    API-only heads (``WORKER_CONCURRENCY=0``) retain the larger request pool.
    Worker processes get an EXPLICITLY SIZED pool from ``WORKER_DB_POOL_SIZE``
    + ``WORKER_DB_MAX_OVERFLOW`` (default ``2`` + ``2`` — exactly the value this
    branch used to hardcode, so an untouched fleet is unchanged). It is
    deliberately NOT derived from ``worker_concurrency``: the binding constraint
    is the shared Postgres on the head, not this process.

    **What 2+2 costs.** A worker must serve ``worker_concurrency`` in-flight
    jobs *plus* its heartbeat loop, the credential limiter and the cost monitor
    out of the same pool, so a ``WORKER_CONCURRENCY=4`` host is capped near 1-2
    real jobs. Measured 2026-08-12 across 33 hosts holding work: 17 ran 1 job,
    14 ran 2, one ran 3, and only ONE ever reached the configured 4; the rest
    surfaced as ``QueuePool limit of size 2 overflow 2 reached, connection timed
    out, timeout 30.00``. Those are RETRYABLE. Raising the pool without upstream
    headroom does not make them go away — it converts them into hard connection
    refusals (``FATAL: sorry, too many clients already``), which also lock the
    head out of its own database. That is why the default did not move.

    **The budget is fleet-wide.** Before raising either setting, compute::

        connections ~= hosts x processes_per_host x (pool_size + max_overflow)

    ``processes_per_host`` is 2-3 here, not 1: a worker box runs several python
    processes and EACH builds its own engine and pool, so every extra process
    multiplies the whole per-process figure (the head is its own case — the API
    and the read-only viewer, which imports this module's ``engine``, are two
    more pools). Measured today: 38 worker hosts already draw ~203 of the head's
    ``max_connections=250`` while running only ~57 concurrent jobs.

    Raising ``WORKER_DB_POOL_SIZE`` / ``WORKER_DB_MAX_OVERFLOW`` therefore
    REQUIRES headroom first — a pgbouncer transaction pooler in front of
    Postgres (planned), or a raised ``max_connections`` — and the sum above,
    recomputed for the fleet's real host and process counts, must fit under that
    ceiling with room left for the head, the viewer and an operator's ``psql``.
    """
    if worker_concurrency == 0:
        return {"pool_size": 20, "max_overflow": 30}
    return {
        "pool_size": settings.worker_db_pool_size,
        "max_overflow": settings.worker_db_max_overflow,
    }


# pool_pre_ping revalidates sockets on checkout; pool_recycle prevents a
# long-lived process from handing out a connection near the server lifetime.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    **_pool_config(worker_concurrency=settings.worker_concurrency),
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
