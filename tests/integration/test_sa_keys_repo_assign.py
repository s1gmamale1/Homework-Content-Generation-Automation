# tests/integration/test_sa_keys_repo_assign.py
import os
import pytest
from app.db import SessionLocal
from app.repositories import sa_keys as repo

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


@pytest.mark.asyncio
async def test_shared_key_assign_scrub_and_lookup():
    async with SessionLocal() as s:
        async with s.begin():
            k = await repo.create_or_get(
                s, original_filename="k.json", project_id="proj-x",
                client_email="e", sha256="SHA-ASG", byte_size=9)
        # one key shared by two hosts (the flexible shared-key case)
        async with s.begin():
            await repo.assign(s, "host-1", k.id)
            await repo.assign(s, "host-2", k.id)
        async with s.begin():
            a1 = await repo.get_assignment_with_key(s, "host-1")
            assert a1["sha256"] == "SHA-ASG" and a1["project_id"] == "proj-x"
            assert a1["scrub"] is False
            assert await repo.get_assignment_with_key(s, "absent-host") is None
        # scrub host-1: key_id cleared, scrub flag set
        async with s.begin():
            await repo.scrub(s, "host-1")
        async with s.begin():
            a1 = await repo.get_assignment_with_key(s, "host-1")
            assert a1["scrub"] is True and a1["key_id"] is None
        # unassign host-2: row gone -> lookup None
        async with s.begin():
            assert await repo.unassign(s, "host-2") is True
        async with s.begin():
            assert await repo.get_assignment_with_key(s, "host-2") is None
