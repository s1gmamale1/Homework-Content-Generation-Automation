import os
import json
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_TOKEN = "T8r2Vw9_Mp4xC7kN1qZ6sH3dL5yF0aJgB-Ue"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _good_key():
    return json.dumps({
        "type": "service_account", "project_id": "asg-proj",
        "client_email": "svc@asg-proj.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_assign_unassign_scrub(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", _TOKEN)
    from main import app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t", headers=_HEADERS
    ) as c:
        kid = (await c.post("/api/v1/sa-keys",
               files={"file": ("k.json", _good_key(), "application/json")})).json()["id"]
        assert (await c.put(f"/api/v1/sa-keys/assignments/host-z",
                            json={"key_id": kid})).status_code == 200
        got = (await c.get("/api/v1/sa-keys/assignments")).json()["assignments"]
        assert any(a["hostname"] == "host-z" and a["project_id"] == "asg-proj" for a in got)
        assert (await c.post(f"/api/v1/sa-keys/assignments/host-z/scrub")).status_code == 200
        assert (await c.delete(f"/api/v1/sa-keys/assignments/host-z")).status_code == 200
        # workers endpoint surfaces assignments
        assert "assignments" in (await c.get("/api/v1/workers")).json()
        # Leave the shared scratch database and per-test vault coherent.  The
        # startup-inventory acceptance that follows this file intentionally
        # fails on DB rows whose canonical UUID object is absent.
        assert (await c.delete(f"/api/v1/sa-keys/{kid}")).status_code == 200
