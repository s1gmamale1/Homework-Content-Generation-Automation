"""Integration test fixtures.

Requires a real PostgreSQL instance. Set TEST_DATABASE_URL before running:

    TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_db pytest tests/integration/

The fixtures here:
  - create all tables before the session
  - wrap each test in a transaction that is rolled back after
  - expose an httpx.AsyncClient pointed at the FastAPI app with auth headers

Install test deps:
    uv add --dev pytest pytest-asyncio httpx anyio
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.base import Base

# ─── Database URL ─────────────────────────────────────────────────────────────
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test_homework",
)

# ─── Engine / session factory ─────────────────────────────────────────────────
@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="session")
async def tables(engine):
    """Create all tables once per test session, drop on teardown."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def session(engine, tables):
    """Per-test async session wrapped in a savepoint that is rolled back."""
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()  # SAVEPOINT

        async_session = async_sessionmaker(
            bind=conn, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as sess:
            yield sess

        await conn.rollback()


# ─── FastAPI test client ───────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(session):
    """httpx.AsyncClient pointed at the FastAPI app with session override."""
    from app.db import get_session
    from main import app

    # Override the DB session dependency so tests use the rollback-wrapped session.
    async def _override_session():
        yield session

    app.dependency_overrides[get_session] = _override_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}
