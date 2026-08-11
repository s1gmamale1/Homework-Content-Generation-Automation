"""Real-Postgres upload ownership and compensation tests for the SA-key vault."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, get_session
from app.models.sa_key import SAKey
from app.services import sa_key_vault, storage
from main import app


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_TOKEN = "T8r2Vw9_Mp4xC7kN1qZ6sH3dL5yF0aJgB-Ue"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _good_key(*, project: str, email: str = "svc") -> bytes:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": project,
            "client_email": f"{email}@{project}.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n",
        }
    ).encode()


@asynccontextmanager
async def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    monkeypatch.setattr(settings, "auth_token", _TOKEN)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_HEADERS,
    ) as client:
        yield client


async def _delete_owned_rows(project: str) -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(SAKey).where(SAKey.project_id == project))
        ).scalars().all()
        for row in rows:
            await session.delete(row)
        await session.commit()


@pytest.mark.asyncio
async def test_two_concurrent_identical_uploads_share_one_owned_row_and_file(
    monkeypatch, tmp_path
):
    """Replacing ON CONFLICT ownership with SELECT-then-INSERT reopens the race."""
    project = f"t4-upload-race-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            one, two = await asyncio.gather(
                client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("a.json", body, "application/json")},
                ),
                client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("b.json", body, "application/json")},
                ),
            )
        assert [one.status_code, two.status_code] == [201, 201]
        assert one.json()["id"] == two.json()["id"]
        async with SessionLocal() as session:
            count = await session.scalar(
                select(func.count()).select_from(SAKey).where(SAKey.project_id == project)
            )
        assert count == 1
        assert sa_key_vault.read_bytes(storage.sa_key_path(one.json()["id"])) == body
    finally:
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_vault_refusal_rolls_back_new_metadata_and_returns_generic_503(
    monkeypatch, tmp_path
):
    """Committing metadata before vault publication would leave a live row without bytes."""
    project = f"t4-upload-refusal-{uuid4()}"
    body = _good_key(project=project)
    monkeypatch.setattr(
        sa_key_vault,
        "atomic_write",
        lambda *_args: (_ for _ in ()).throw(
            sa_key_vault.SAKeyVaultError("private/path/detail")
        ),
    )
    try:
        async with _client(monkeypatch, tmp_path) as client:
            response = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
        assert response.status_code == 503
        assert response.json() == {"detail": "SA-key vault is unavailable"}
        assert "private/path/detail" not in response.text
        async with SessionLocal() as session:
            count = await session.scalar(
                select(func.count()).select_from(SAKey).where(SAKey.project_id == project)
            )
        assert count == 0
    finally:
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_new_upload_publishes_vault_bytes_before_database_commit(
    monkeypatch, tmp_path
):
    """Reordering commit before publication would create a live row without bytes."""
    project = f"t4-upload-order-{uuid4()}"
    body = _good_key(project=project)
    order = []
    real_atomic_write = sa_key_vault.atomic_write

    def record_atomic(path, payload):
        order.append("atomic_write")
        real_atomic_write(path, payload)

    async def recording_session():
        async with SessionLocal() as session:
            real_commit = session.commit

            async def record_commit():
                order.append("commit")
                await real_commit()

            monkeypatch.setattr(session, "commit", record_commit)
            yield session

    monkeypatch.setattr(sa_key_vault, "atomic_write", record_atomic)
    app.dependency_overrides[get_session] = recording_session
    try:
        async with _client(monkeypatch, tmp_path) as client:
            response = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
        assert response.status_code == 201
        assert order == ["atomic_write", "commit"]
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_removes_only_newly_owned_file(
    monkeypatch, tmp_path
):
    """Compensation must use pinned scalars after rollback and a fresh definitive read."""
    project = f"t4-upload-commit-{uuid4()}"
    body = _good_key(project=project)
    sha = hashlib.sha256(body).hexdigest()
    rollback_calls = []

    async def failing_session():
        async with SessionLocal() as session:
            real_rollback = session.rollback

            async def fail_commit():
                await session.flush()
                raise RuntimeError("forced commit failure")

            async def record_rollback():
                rollback_calls.append(True)
                await real_rollback()

            monkeypatch.setattr(session, "commit", fail_commit)
            monkeypatch.setattr(session, "rollback", record_rollback)
            yield session

    app.dependency_overrides[get_session] = failing_session
    try:
        async with _client(monkeypatch, tmp_path) as client:
            response = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
        assert response.status_code == 503
        assert response.json() == {"detail": "SA-key upload did not commit"}
        assert rollback_calls == [True]
        async with SessionLocal() as session:
            row = await session.scalar(select(SAKey).where(SAKey.sha256 == sha))
        assert row is None
        assert list((tmp_path / "sa_keys").glob("*.json")) == []
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_existing_dedup_commit_failure_never_compensates_owned_bytes(
    monkeypatch, tmp_path
):
    """A failed re-upload must not delete a file owned by an older committed row."""
    project = f"t4-upload-existing-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            seeded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("first.json", body, "application/json")},
            )
            key_id = seeded.json()["id"]

            async def failing_session():
                async with SessionLocal() as session:
                    real_rollback = session.rollback

                    async def fail_commit():
                        raise RuntimeError("forced dedup commit failure")

                    monkeypatch.setattr(session, "commit", fail_commit)
                    monkeypatch.setattr(session, "rollback", real_rollback)
                    yield session

            app.dependency_overrides[get_session] = failing_session
            failed = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("again.json", body, "application/json")},
            )
        assert failed.status_code == 503
        async with SessionLocal() as session:
            row = await session.get(SAKey, key_id)
            assert row is not None
        assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_existing_dedup_repairs_missing_bytes_from_same_validated_body(
    monkeypatch, tmp_path
):
    """A committed metadata row with missing bytes is repaired under its row lock."""
    project = f"t4-upload-repair-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            seeded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("first.json", body, "application/json")},
            )
            key_id = seeded.json()["id"]
            sa_key_vault.remove(storage.sa_key_path(key_id))
            repaired = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("again.json", body, "application/json")},
            )
        assert repaired.status_code == 201
        assert repaired.json()["id"] == key_id
        assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
    finally:
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_existing_dedup_repairs_wrong_hash_bytes_from_validated_body(
    monkeypatch, tmp_path
):
    """A mismatched file is replaced only by the body that hashes to row.sha256."""
    project = f"t4-upload-wrong-hash-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            seeded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("first.json", body, "application/json")},
            )
            key_id = seeded.json()["id"]
            sa_key_vault.atomic_write(storage.sa_key_path(key_id), b"wrong bytes")
            repaired = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("again.json", body, "application/json")},
            )
        assert repaired.status_code == 201
        assert repaired.json()["id"] == key_id
        assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
    finally:
        await _delete_owned_rows(project)


@pytest.mark.asyncio
async def test_upload_compensation_keeps_wrong_hash_evidence(monkeypatch, tmp_path):
    """Fresh DB absence does not authorize deleting bytes unlike our pinned SHA."""
    from app.api.v1.sa_keys import _compensate_new_upload_if_definitively_uncommitted

    monkeypatch.setattr(settings, "var_dir", str(tmp_path))
    row_id = uuid4()
    path = storage.sa_key_path(row_id)
    sa_key_vault.atomic_write(path, b"different evidence")

    await _compensate_new_upload_if_definitively_uncommitted(
        row_id=row_id,
        sha256=hashlib.sha256(b"our upload").hexdigest(),
        created=True,
    )

    assert sa_key_vault.read_bytes(path) == b"different evidence"


@pytest.mark.asyncio
async def test_uuid_hash_inventory_returns_exact_canonical_names(monkeypatch, tmp_path):
    """Startup reconciliation must receive one canonical filename per DB row."""
    from app.repositories import sa_keys as repo

    project = f"t4-upload-inventory-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            uploaded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
        key_id = uploaded.json()["id"]
        async with SessionLocal() as session:
            inventory = await repo.uuid_hash_inventory(session)
        assert inventory[f"{key_id}.json"] == hashlib.sha256(body).hexdigest()
        assert all(name.endswith(".json") for name in inventory)
    finally:
        await _delete_owned_rows(project)
