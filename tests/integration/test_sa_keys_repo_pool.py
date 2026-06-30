import os

import pytest
from app.db import SessionLocal
from app.repositories import sa_keys as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_create_dedups_on_sha_and_delete_guards_assigned():
    async with SessionLocal() as s:
        async with s.begin():
            a = await repo.create_or_get(
                s,
                original_filename="k.json",
                project_id="p1",
                client_email="e1",
                sha256="SHA-POOL-1",
                byte_size=10,
            )
            b = await repo.create_or_get(
                s,
                original_filename="k2.json",
                project_id="p1",
                client_email="e1",
                sha256="SHA-POOL-1",
                byte_size=10,
            )
        assert a.id == b.id  # dedup hit, same row

    async with SessionLocal() as s:
        async with s.begin():
            listed = await repo.list_keys(s)
        assert any(k["id"] == a.id and k["worker_count"] == 0 for k in listed)
        assert all("private_key" not in k for k in listed)

    async with SessionLocal() as s:
        async with s.begin():
            await repo.assign(s, "host-pool", a.id)
        async with s.begin():
            assert await repo.delete(s, a.id) == "assigned"  # blocked
        async with s.begin():
            await repo.unassign(s, "host-pool")
        async with s.begin():
            assert await repo.delete(s, a.id) == "deleted"
