from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

def _pool_config(*, worker_concurrency: int) -> dict[str, int]:
    """Return role-appropriate pool bounds.

    API-only heads (``WORKER_CONCURRENCY=0``) retain the larger request pool.
    Worker processes use short database transactions around model calls, so
    retaining a 20-connection idle pool per host only exhausts Postgres across
    the fleet. Two pooled connections plus two temporary overflow connections
    cover worker heartbeats and phase persistence without permanent residue.
    """
    if worker_concurrency == 0:
        return {"pool_size": 20, "max_overflow": 30}
    return {"pool_size": 2, "max_overflow": 2}


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
