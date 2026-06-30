import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_sa_keys_tables_and_constraints():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    kid1, kid2 = uuid.uuid4(), uuid.uuid4()

    # --- tables exist ---
    async with engine.begin() as conn:
        for t in ("sa_keys", "sa_key_assignments"):
            got = await conn.scalar(text("SELECT to_regclass(:t)"), {"t": t})
            assert got == t

    # --- insert kid1 row (committed) ---
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sa_keys (id, original_filename, project_id, client_email, "
            "sha256, byte_size, created_at) VALUES (:id,'a','p','e','SHA',10, now())"
        ), {"id": kid1})

    # --- unique sha256: a second identical sha must fail (isolated transaction) ---
    async with engine.begin() as conn:
        with pytest.raises(Exception):
            await conn.execute(text(
                "INSERT INTO sa_keys (id, original_filename, project_id, client_email, "
                "sha256, byte_size, created_at) VALUES (:id,'b','p','e','SHA',10, now())"
            ), {"id": kid2})

    # --- key_id is nullable + FK RESTRICT blocks deleting an assigned key ---
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sa_key_assignments (hostname, key_id, updated_at) "
            "VALUES ('host-a', :kid, now())"
        ), {"kid": kid1})

    async with engine.begin() as conn:
        with pytest.raises(Exception):
            await conn.execute(text("DELETE FROM sa_keys WHERE id=:kid"), {"kid": kid1})

    # --- null key_id (scrub state) is allowed ---
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sa_key_assignments (hostname, key_id, scrub_requested_at, updated_at) "
            "VALUES ('host-b', NULL, now(), now())"
        ))

    await engine.dispose()
