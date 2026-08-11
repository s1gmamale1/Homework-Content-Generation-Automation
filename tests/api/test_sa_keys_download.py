import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)


def _good_key():
    return json.dumps({
        "type": "service_account", "project_id": "dl-proj",
        "client_email": "svc@dl-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_download_is_header_only(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", "secret")
    from main import app
    H = {"Authorization": "Bearer secret"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        kid = (await c.post("/api/v1/sa-keys",
               files={"file": ("k.json", _good_key(), "application/json")}, headers=H)).json()["id"]
        # ?token= still works on a normal endpoint (proves query auth was not
        # globally removed when the complete SA-key router became header-only).
        assert (await c.get("/api/v1/books?token=secret")).status_code == 200
        # The SA-key collection itself is now header-only too.
        assert (await c.get("/api/v1/sa-keys?token=secret")).status_code == 401
        # ?token= is REJECTED on the download route (header-only)
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download?token=secret")).status_code == 401
        # correct header serves the bytes
        r = await c.get(f"/api/v1/sa-keys/{kid}/download", headers=H)
        assert r.status_code == 200 and json.loads(r.content)["project_id"] == "dl-proj"
        # missing header -> 401
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download")).status_code == 401
