import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key(project="proj-api"):
    return json.dumps({
        "type": "service_account", "project_id": project,
        "client_email": f"svc@{project}.iam.gserviceaccount.com",
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
