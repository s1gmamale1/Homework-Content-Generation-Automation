import os
import json
import uuid
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key(project="proj-api", email="svc"):
    return json.dumps({
        "type": "service_account", "project_id": project,
        "client_email": f"{email}@{project}.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_upload_validates_dedups_and_lists_without_private_key(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")  # auth disabled for the test
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # reject a non-SA file
        r = await c.post("/api/v1/sa-keys", files={"file": ("bad.json", b"{}", "application/json")})
        assert r.status_code == 422
        # accept a good key, project auto-extracted
        r = await c.post("/api/v1/sa-keys", files={"file": ("k.json", _good_key(), "application/json")})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["project_id"] == "proj-api" and "private_key" not in body
        kid = body["id"]
        # the bytes landed on disk
        import app.services.storage as storage
        assert storage.sa_key_path(kid).exists()
        # re-upload identical bytes dedups to the same id
        r2 = await c.post("/api/v1/sa-keys", files={"file": ("k.json", _good_key(), "application/json")})
        assert r2.json()["id"] == kid
        # list never leaks private_key
        r = await c.get("/api/v1/sa-keys")
        assert all("private_key" not in k for k in r.json()["keys"])
        # delete works (unassigned)
        assert (await c.delete(f"/api/v1/sa-keys/{kid}")).status_code == 200


@pytest.mark.asyncio
async def test_patch_max_concurrent_calls_updates_project_wide(monkeypatch, tmp_path):
    """codex #2 (binding): PATCH on one key updates EVERY sa_keys row
    sharing that key's project_id, atomically, and reports how many rows
    it touched. Two distinct SA-key JSON files (different client_email so
    the upload sha256 differs) but the SAME project_id — the real-world
    "two service accounts for one GCP project" shape."""
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r1 = await c.post(
            "/api/v1/sa-keys",
            files={"file": ("a.json", _good_key(project="proj-patch-shared", email="svc-a"), "application/json")},
        )
        r2 = await c.post(
            "/api/v1/sa-keys",
            files={"file": ("b.json", _good_key(project="proj-patch-shared", email="svc-b"), "application/json")},
        )
        assert r1.status_code == 201 and r2.status_code == 201
        id1, id2 = r1.json()["id"], r2.json()["id"]

        r = await c.patch(f"/api/v1/sa-keys/{id1}", json={"max_concurrent_calls": 5})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_concurrent_calls"] == 5
        assert body["rows_updated"] == 2  # both rows, same project

        listed = (await c.get("/api/v1/sa-keys")).json()["keys"]
        both = [k for k in listed if k["id"] in (id1, id2)]
        assert len(both) == 2
        assert all(k["max_concurrent_calls"] == 5 for k in both)

        # PATCH with null clears the override on both rows again.
        r = await c.patch(f"/api/v1/sa-keys/{id2}", json={"max_concurrent_calls": None})
        assert r.status_code == 200
        assert r.json()["rows_updated"] == 2
        assert r.json()["max_concurrent_calls"] is None

        await c.delete(f"/api/v1/sa-keys/{id1}")
        await c.delete(f"/api/v1/sa-keys/{id2}")


@pytest.mark.asyncio
async def test_patch_404_for_missing_key(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.patch(f"/api/v1/sa-keys/{uuid.uuid4()}", json={"max_concurrent_calls": 3})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_422_for_sub_one_value(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/sa-keys",
            files={"file": ("k.json", _good_key(project="proj-patch-422", email="svc-422"), "application/json")},
        )
        kid = r.json()["id"]

        for bad in (0, -1):
            r = await c.patch(f"/api/v1/sa-keys/{kid}", json={"max_concurrent_calls": bad})
            assert r.status_code == 422, r.text

        await c.delete(f"/api/v1/sa-keys/{kid}")


@pytest.mark.asyncio
async def test_list_serves_slots_in_use_and_effective_limit(monkeypatch, tmp_path):
    import app.config as config
    from app.db import SessionLocal
    from app.services import credential_limiter
    from sqlalchemy import text as sa_text

    credential_limiter.clear_limit_cache()
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "")
    # Deterministic default so the test doesn't depend on the ambient .env.
    monkeypatch.setattr(config.settings, "credential_max_concurrent_gemini", 8)
    from main import app
    transport = ASGITransport(app=app)

    project = f"proj-slots-{uuid.uuid4().hex[:8]}"
    credential = f"gemini:{project}"
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(
            "/api/v1/sa-keys",
            files={"file": ("k.json", _good_key(project=project, email="svc-slots"), "application/json")},
        )
        kid = r.json()["id"]

        # Seed two fresh in-flight slots for this key's project credential.
        async with SessionLocal() as s:
            async with s.begin():
                for pc in ("pc-x", "pc-y"):
                    await s.execute(
                        sa_text(
                            "INSERT INTO credential_slots (credential, pc_id, acquired_at) "
                            "VALUES (:cred, :pc, now())"
                        ),
                        {"cred": credential, "pc": pc},
                    )

        try:
            listed = (await c.get("/api/v1/sa-keys")).json()["keys"]
            row = next(k for k in listed if k["id"] == kid)
            assert row["slots_in_use"] == 2
            assert row["effective_limit"] == 8  # no override -> provider default

            # Setting an override changes the effective_limit too. PATCH
            # itself must evict the ~60s resolve_limit cache (review fix,
            # task 6) — no manual clear here; if production didn't evict,
            # this read would return the stale pre-override value.
            await c.patch(f"/api/v1/sa-keys/{kid}", json={"max_concurrent_calls": 3})
            listed = (await c.get("/api/v1/sa-keys")).json()["keys"]
            row = next(k for k in listed if k["id"] == kid)
            assert row["effective_limit"] == 3
        finally:
            async with SessionLocal() as s:
                async with s.begin():
                    await s.execute(
                        sa_text("DELETE FROM credential_slots WHERE credential = :cred"),
                        {"cred": credential},
                    )
            await c.delete(f"/api/v1/sa-keys/{kid}")
