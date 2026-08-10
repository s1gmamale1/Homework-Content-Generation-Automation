import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import _connection_server_settings


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="set RUN_DB_INTEGRATION=1 with a scratch DATABASE_URL",
)


@pytest.mark.asyncio
async def test_asyncpg_applies_session_settings() -> None:
    expected = _connection_server_settings(
        worker_concurrency=2, hostname="soak-test", pid=404
    )
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        pool_size=1,
        max_overflow=0,
        connect_args={"server_settings": expected},
    )
    try:
        async with engine.connect() as conn:
            app_name = await conn.scalar(
                text("select current_setting('application_name')")
            )
            idle_timeout = await conn.scalar(
                text(
                    "select current_setting("
                    "'idle_in_transaction_session_timeout')"
                )
            )
        assert app_name == "hcga-worker:soak-test:404"
        assert idle_timeout == "5min"
    finally:
        await engine.dispose()
