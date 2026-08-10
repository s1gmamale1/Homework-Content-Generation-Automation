import os
import socket
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


_IDLE_IN_TRANSACTION_TIMEOUT_MS = 300_000


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


def _application_name(*, worker_concurrency: int, hostname: str, pid: int) -> str:
    role = "head" if worker_concurrency == 0 else "worker"
    prefix = f"hcga-{role}:"
    suffix = f":{pid}"
    hostname_byte_budget = 63 - len(prefix.encode()) - len(suffix.encode())
    safe_hostname = hostname.encode("utf-8")[:max(hostname_byte_budget, 0)].decode(
        "utf-8", errors="ignore"
    )
    return f"{prefix}{safe_hostname}{suffix}"


def _connection_server_settings(
    *, worker_concurrency: int, hostname: str, pid: int
) -> dict[str, str]:
    return {
        "application_name": _application_name(
            worker_concurrency=worker_concurrency,
            hostname=hostname,
            pid=pid,
        ),
        "idle_in_transaction_session_timeout": str(
            _IDLE_IN_TRANSACTION_TIMEOUT_MS
        ),
    }


def _engine_options(
    *, worker_concurrency: int, hostname: str, pid: int
) -> dict[str, object]:
    return {
        **_pool_config(worker_concurrency=worker_concurrency),
        "connect_args": {
            "server_settings": _connection_server_settings(
                worker_concurrency=worker_concurrency,
                hostname=hostname,
                pid=pid,
            )
        },
    }


# pool_pre_ping revalidates sockets on checkout; pool_recycle prevents a
# long-lived process from handing out a connection near the server lifetime.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    **_engine_options(
        worker_concurrency=settings.worker_concurrency,
        hostname=socket.gethostname(),
        pid=os.getpid(),
    ),
    pool_pre_ping=True,
    pool_recycle=1800,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
