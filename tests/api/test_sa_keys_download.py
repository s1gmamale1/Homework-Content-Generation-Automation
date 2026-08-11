import os
import json
from uuid import uuid4
import pytest
from httpx import AsyncClient, ASGITransport

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_TOKEN = "T8r2Vw9_Mp4xC7kN1qZ6sH3dL5yF0aJgB-Ue"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _good_key(project="dl-proj"):
    return json.dumps({
        "type": "service_account", "project_id": project,
        "client_email": f"svc@{project}.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
    }).encode()


@pytest.mark.asyncio
async def test_download_is_header_only(monkeypatch, tmp_path):
    import app.config as config
    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", _TOKEN)
    from main import app
    H = _HEADERS
    project = f"dl-proj-{uuid4()}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        kid = (await c.post("/api/v1/sa-keys",
               files={"file": ("k.json", _good_key(project), "application/json")}, headers=H)).json()["id"]
        # ?token= still works on a normal endpoint (proves query auth was not
        # globally removed when the complete SA-key router became header-only).
        assert (await c.get(f"/api/v1/books?token={_TOKEN}")).status_code == 200
        # The SA-key collection itself is now header-only too.
        assert (await c.get(f"/api/v1/sa-keys?token={_TOKEN}")).status_code == 401
        # ?token= is REJECTED on the download route (header-only)
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download?token={_TOKEN}")).status_code == 401
        # correct header serves the bytes
        r = await c.get(f"/api/v1/sa-keys/{kid}/download", headers=H)
        assert r.status_code == 200 and json.loads(r.content)["project_id"] == project
        # missing header -> 401
        assert (await c.get(f"/api/v1/sa-keys/{kid}/download")).status_code == 401
        assert (await c.delete(f"/api/v1/sa-keys/{kid}", headers=H)).status_code == 200


@pytest.mark.asyncio
async def test_download_vault_refusal_is_generic_503(monkeypatch, tmp_path):
    import app.config as config
    from app.services import sa_key_vault
    from main import app

    monkeypatch.setattr(config.settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(config.settings, "auth_token", _TOKEN)
    project = f"dl-refusal-{uuid4()}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        kid = (
            await c.post(
                "/api/v1/sa-keys",
                files={"file": ("k.json", _good_key(project), "application/json")},
                headers=_HEADERS,
            )
        ).json()["id"]
        real_read = sa_key_vault.read_bytes
        monkeypatch.setattr(
            sa_key_vault,
            "read_bytes",
            lambda *_args: (_ for _ in ()).throw(
                sa_key_vault.SAKeyVaultError("private/path/detail")
            ),
        )
        response = await c.get(
            f"/api/v1/sa-keys/{kid}/download", headers=_HEADERS
        )
        monkeypatch.setattr(sa_key_vault, "read_bytes", real_read)
        assert (
            await c.delete(f"/api/v1/sa-keys/{kid}", headers=_HEADERS)
        ).status_code == 200
    assert response.status_code == 503
    assert response.json() == {"detail": "SA-key vault is unavailable"}
    assert "private/path/detail" not in response.text
