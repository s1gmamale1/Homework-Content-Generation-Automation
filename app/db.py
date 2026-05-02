from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Pool sized for the parallel phase scheduler: a single HARD job can have
# 8 content phases in flight concurrently, each opening 2-4 sessions during
# its lifetime (status updates, JSON persistence, etc). The default
# pool_size=5 / max_overflow=10 was sized for a sequential pipeline and
# starves under the new wave-based scheduler.
#
# pool_pre_ping=True: revalidate connections on checkout. Long-running phases
# can leave a connection idle for >30s, by which point Postgres' default
# `idle_in_transaction_session_timeout` (or the server's keepalive) may have
# closed it server-side. Without pre-ping the pool hands out a dead socket
# → "connection is closed" InterfaceError, exactly as we just hit.
#
# pool_recycle=1800: proactively recycle connections every 30 min so we never
# hand out one approaching Postgres' connection lifetime limit.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
