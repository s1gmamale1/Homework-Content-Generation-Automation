"""Real-Postgres assignment/delete serialization and vault quarantine tests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, get_session
from app.models.sa_key import SAKey, SAKeyAssignment
from app.services import sa_key_vault, storage
from main import app


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_TOKEN = "T8r2Vw9_Mp4xC7kN1qZ6sH3dL5yF0aJgB-Ue"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}


def _good_key(*, project: str) -> bytes:
    return json.dumps(
        {
            "type": "service_account",
            "project_id": project,
            "client_email": f"svc@{project}.iam.gserviceaccount.com",
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


async def _cleanup(project: str, hostname: str) -> None:
    async with SessionLocal() as session:
        assignment = await session.get(SAKeyAssignment, hostname)
        if assignment is not None:
            await session.delete(assignment)
            await session.flush()
        rows = (
            await session.execute(select(SAKey).where(SAKey.project_id == project))
        ).scalars().all()
        for row in rows:
            await session.delete(row)
        await session.commit()


@pytest.mark.asyncio
async def test_unassigned_delete_quarantines_before_commit_then_discards(
    monkeypatch, tmp_path
):
    """Direct unlink or quarantine-after-commit would violate crash recovery ordering."""
    project = f"t4-delete-order-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    order = []
    real_quarantine = sa_key_vault.quarantine_for_delete
    real_discard = sa_key_vault.discard_quarantined_delete

    def quarantine(path, *, expected_sha256):
        order.append("quarantine")
        return real_quarantine(path, expected_sha256=expected_sha256)

    def discard(ticket):
        order.append("discard")
        return real_discard(ticket)

    async def recording_session():
        async with SessionLocal() as session:
            real_commit = session.commit

            async def record_commit():
                order.append("commit")
                await real_commit()

            monkeypatch.setattr(session, "commit", record_commit)
            yield session

    monkeypatch.setattr(sa_key_vault, "quarantine_for_delete", quarantine)
    monkeypatch.setattr(sa_key_vault, "discard_quarantined_delete", discard)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            uploaded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
            key_id = uploaded.json()["id"]
            app.dependency_overrides[get_session] = recording_session
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 200
        assert order == ["quarantine", "commit", "discard"]
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is None
        assert not storage.sa_key_path(key_id).exists()
        assert list((tmp_path / "sa_keys").glob("*.delete-quarantine")) == []
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_assigned_delete_returns_409_before_quarantine(monkeypatch, tmp_path):
    """Moving bytes before checking assignments could strand a live worker binding."""
    project = f"t4-delete-assigned-{uuid4()}"
    hostname = f"t4-host-{uuid4()}"
    body = _good_key(project=project)
    calls = []
    monkeypatch.setattr(
        sa_key_vault,
        "quarantine_for_delete",
        lambda *_args, **_kwargs: calls.append(True),
    )
    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            assigned = await client.put(
                f"/api/v1/sa-keys/assignments/{hostname}", json={"key_id": key_id}
            )
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert assigned.status_code == 200
        assert response.status_code == 409
        assert calls == []
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is not None
            assignment = await session.get(SAKeyAssignment, hostname)
            assert str(assignment.key_id) == key_id
        assert storage.sa_key_path(key_id).exists()
    finally:
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_quarantine_refusal_keeps_row_and_file_and_returns_generic_503(
    monkeypatch, tmp_path
):
    """A vault refusal before DB deletion must preserve both authorities."""
    project = f"t4-delete-refusal-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            monkeypatch.setattr(
                sa_key_vault,
                "quarantine_for_delete",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    sa_key_vault.SAKeyVaultError("private/path/detail")
                ),
            )
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 503
        assert response.json() == {"detail": "SA-key vault is unavailable"}
        assert "private/path/detail" not in response.text
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is not None
        assert storage.sa_key_path(key_id).exists()
    finally:
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_delete_failure_before_commit_leaves_startup_to_restore_quarantine(
    monkeypatch, tmp_path
):
    """A normal rollback return still leaves recovery to startup authority."""
    project = f"t4-delete-rollback-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    sha = hashlib.sha256(body).hexdigest()

    async def failing_session():
        async with SessionLocal() as session:
            real_rollback = session.rollback

            async def fail_commit():
                await session.flush()
                raise RuntimeError("forced delete commit failure")

            monkeypatch.setattr(session, "commit", fail_commit)
            monkeypatch.setattr(session, "rollback", real_rollback)
            yield session

    try:
        async with _client(monkeypatch, tmp_path) as client:
            seeded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
            key_id = seeded.json()["id"]
            app.dependency_overrides[get_session] = failing_session
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 503
        async with SessionLocal() as session:
            row = await session.get(SAKey, key_id)
            assert row is not None
        assert not storage.sa_key_path(key_id).exists()
        quarantines = list((tmp_path / "sa_keys").glob("*.delete-quarantine"))
        assert len(quarantines) == 1
        sa_key_vault.reconcile_delete_quarantines({key_id: sha})
        sa_key_vault.verify_uuid_inventory({key_id: sha})
        assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
        assert list((tmp_path / "sa_keys").glob("*.delete-quarantine")) == []
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _cleanup(project, hostname)


@pytest.mark.asyncio
@pytest.mark.parametrize("eventual_outcome", ["commit", "rollback"])
@pytest.mark.parametrize("rollback_behavior", ["raise", "noop"])
async def test_delete_rollback_error_preserves_quarantine_until_original_tx_resolves(
    monkeypatch, tmp_path, eventual_outcome, rollback_behavior
):
    """Fresh live-row visibility cannot prove an unresolved DELETE rolled back."""
    from app.api.v1 import sa_keys as api

    project = f"t4-delete-rollback-error-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    sha = hashlib.sha256(body).hexdigest()
    captured = []
    real_quarantine = sa_key_vault.quarantine_for_delete

    def capture(path, *, expected_sha256):
        ticket = real_quarantine(path, expected_sha256=expected_sha256)
        captured.append(ticket)
        return ticket

    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
        monkeypatch.setattr(sa_key_vault, "quarantine_for_delete", capture)
        async with SessionLocal() as request_session:
            real_commit = request_session.commit
            real_rollback = request_session.rollback

            async def fail_commit():
                await request_session.flush()
                raise RuntimeError("private commit detail")

            async def fail_rollback():
                if rollback_behavior == "raise":
                    raise RuntimeError("private rollback detail")

            monkeypatch.setattr(request_session, "commit", fail_commit)
            monkeypatch.setattr(request_session, "rollback", fail_rollback)
            with pytest.raises(HTTPException) as raised:
                await api.delete_sa_key(UUID(key_id), request_session)
            assert raised.value.status_code == 503
            assert raised.value.detail == "SA-key delete outcome is unavailable"
            assert len(captured) == 1
            ticket = captured[0]

            # The fresh session still sees the pre-delete row, but restoring
            # now would become an orphan canonical file if this tx commits.
            async with SessionLocal() as fresh:
                assert await fresh.get(SAKey, key_id) is not None
            assert not storage.sa_key_path(key_id).exists()
            assert (tmp_path / "sa_keys" / ticket.quarantine_name).exists()

            if eventual_outcome == "commit":
                await real_commit()
            else:
                await real_rollback()

        async with SessionLocal() as session:
            resolved_row = await session.get(SAKey, key_id)
        expected = {} if eventual_outcome == "commit" else {key_id: sha}
        if eventual_outcome == "commit":
            assert resolved_row is None
        else:
            assert resolved_row is not None
        sa_key_vault.reconcile_delete_quarantines(expected)
        sa_key_vault.verify_uuid_inventory(expected)
        assert not (tmp_path / "sa_keys" / ticket.quarantine_name).exists()
        if eventual_outcome == "rollback":
            assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
    finally:
        await _cleanup(project, hostname)


@pytest.mark.asyncio
@pytest.mark.parametrize("vault_state", ["missing", "mismatch"])
async def test_assignment_refuses_live_row_without_matching_canonical_bytes(
    monkeypatch, tmp_path, vault_state
):
    """A quarantined or corrupted key cannot become worker-claimable."""
    project = f"t4-assign-vault-guard-{uuid4()}"
    hostname = f"t4-assign-vault-host-{uuid4()}"
    body = _good_key(project=project)
    ticket = None
    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            path = storage.sa_key_path(key_id)
            if vault_state == "missing":
                ticket = sa_key_vault.quarantine_for_delete(
                    path, expected_sha256=hashlib.sha256(body).hexdigest()
                )
            else:
                sa_key_vault.atomic_write(path, b"wrong bytes")

            response = await client.put(
                f"/api/v1/sa-keys/assignments/{hostname}",
                json={"key_id": key_id},
            )

        assert response.status_code == 503
        assert response.json() == {"detail": "SA-key vault is unavailable"}
        async with SessionLocal() as session:
            assert await session.get(SAKeyAssignment, hostname) is None
        if ticket is not None:
            assert (tmp_path / "sa_keys" / ticket.quarantine_name).exists()
    finally:
        if ticket is not None:
            sa_key_vault.restore_quarantined_delete(ticket)
        elif "key_id" in locals():
            sa_key_vault.atomic_write(storage.sa_key_path(key_id), body)
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_delete_ambiguous_commit_and_rollback_errors_wait_for_startup_discard(
    monkeypatch, tmp_path
):
    """Rollback uncertainty preserves quarantine until startup sees DB absence."""
    project = f"t4-delete-double-error-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    captured = []
    real_quarantine = sa_key_vault.quarantine_for_delete

    def capture(path, *, expected_sha256):
        ticket = real_quarantine(path, expected_sha256=expected_sha256)
        captured.append(ticket)
        return ticket

    async def ambiguous_session():
        async with SessionLocal() as session:
            real_commit = session.commit

            async def commit_then_raise():
                await real_commit()
                raise RuntimeError("private after-commit detail")

            async def fail_rollback():
                raise RuntimeError("private rollback detail")

            monkeypatch.setattr(session, "commit", commit_then_raise)
            monkeypatch.setattr(session, "rollback", fail_rollback)
            yield session

    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            monkeypatch.setattr(sa_key_vault, "quarantine_for_delete", capture)
            app.dependency_overrides[get_session] = ambiguous_session
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 503
        assert len(captured) == 1
        ticket = captured[0]
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is None
        assert (tmp_path / "sa_keys" / ticket.quarantine_name).exists()
        assert not storage.sa_key_path(key_id).exists()
        sa_key_vault.reconcile_delete_quarantines({})
        sa_key_vault.verify_uuid_inventory({})
        assert not (tmp_path / "sa_keys" / ticket.quarantine_name).exists()
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_exception_after_real_commit_leaves_startup_to_discard_quarantine(
    monkeypatch, tmp_path
):
    """Even known DB absence is acted on by startup, not the request handler."""
    project = f"t4-delete-after-commit-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)

    async def ambiguous_session():
        async with SessionLocal() as session:
            real_commit = session.commit
            real_rollback = session.rollback

            async def commit_then_raise():
                await real_commit()
                raise RuntimeError("connection lost after commit")

            monkeypatch.setattr(session, "commit", commit_then_raise)
            monkeypatch.setattr(session, "rollback", real_rollback)
            yield session

    try:
        async with _client(monkeypatch, tmp_path) as client:
            seeded = await client.post(
                "/api/v1/sa-keys",
                files={"file": ("key.json", body, "application/json")},
            )
            key_id = seeded.json()["id"]
            app.dependency_overrides[get_session] = ambiguous_session
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 503
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is None
        assert not storage.sa_key_path(key_id).exists()
        assert len(list((tmp_path / "sa_keys").glob("*.delete-quarantine"))) == 1
        sa_key_vault.reconcile_delete_quarantines({})
        sa_key_vault.verify_uuid_inventory({})
        assert list((tmp_path / "sa_keys").glob("*.delete-quarantine")) == []
    finally:
        app.dependency_overrides.pop(get_session, None)
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_concurrent_assign_delete_finishes_in_one_whole_state(
    monkeypatch, tmp_path
):
    """Dropping either key-row lock can create a dangling assignment or missing file."""
    project = f"t4-delete-race-{uuid4()}"
    hostname = f"t4-host-race-{uuid4()}"
    body = _good_key(project=project)
    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            assigned, deleted = await asyncio.gather(
                client.put(
                    f"/api/v1/sa-keys/assignments/{hostname}",
                    json={"key_id": key_id},
                ),
                client.delete(f"/api/v1/sa-keys/{key_id}"),
            )
        async with SessionLocal() as session:
            row = await session.get(SAKey, key_id)
            assignment = await session.get(SAKeyAssignment, hostname)
        if assigned.status_code == 200:
            assert deleted.status_code == 409
            assert row is not None and assignment is not None
            assert str(assignment.key_id) == key_id
            assert storage.sa_key_path(key_id).exists()
        else:
            assert assigned.status_code == 404
            assert deleted.status_code == 200
            assert row is None and assignment is None
            assert not storage.sa_key_path(key_id).exists()
    finally:
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_post_commit_discard_failure_retains_exact_quarantine(
    monkeypatch, tmp_path
):
    """Once DB deletion commits, cleanup failure is visible and evidence remains."""
    project = f"t4-delete-discard-{uuid4()}"
    hostname = f"t4-unused-{uuid4()}"
    body = _good_key(project=project)
    captured = []
    real_quarantine = sa_key_vault.quarantine_for_delete
    real_discard = sa_key_vault.discard_quarantined_delete

    def capture(path, *, expected_sha256):
        ticket = real_quarantine(path, expected_sha256=expected_sha256)
        captured.append(ticket)
        return ticket

    try:
        async with _client(monkeypatch, tmp_path) as client:
            key_id = (
                await client.post(
                    "/api/v1/sa-keys",
                    files={"file": ("key.json", body, "application/json")},
                )
            ).json()["id"]
            monkeypatch.setattr(sa_key_vault, "quarantine_for_delete", capture)
            monkeypatch.setattr(
                sa_key_vault,
                "discard_quarantined_delete",
                lambda _ticket: (_ for _ in ()).throw(
                    sa_key_vault.SAKeyVaultError("forced discard refusal")
                ),
            )
            response = await client.delete(f"/api/v1/sa-keys/{key_id}")
        assert response.status_code == 503
        assert len(captured) == 1
        ticket = captured[0]
        async with SessionLocal() as session:
            assert await session.get(SAKey, key_id) is None
        assert (tmp_path / "sa_keys" / ticket.quarantine_name).exists()
        monkeypatch.setattr(sa_key_vault, "discard_quarantined_delete", real_discard)
        real_discard(ticket)
    finally:
        await _cleanup(project, hostname)


@pytest.mark.asyncio
async def test_delete_preserves_all_neighbor_files_rows_and_assignment(
    monkeypatch, tmp_path
):
    """Deleting one synthetic key must not disturb the other five vault objects."""
    prefix = f"t4-six-{uuid4()}"
    hostname = f"t4-six-host-{uuid4()}"
    created = []
    try:
        async with _client(monkeypatch, tmp_path) as client:
            for index in range(6):
                project = f"{prefix}-{index}"
                body = _good_key(project=project)
                response = await client.post(
                    "/api/v1/sa-keys",
                    files={"file": (f"{index}.json", body, "application/json")},
                )
                created.append((response.json()["id"], body))
            assigned_id = created[-1][0]
            assert (
                await client.put(
                    f"/api/v1/sa-keys/assignments/{hostname}",
                    json={"key_id": assigned_id},
                )
            ).status_code == 200

            assert (
                await client.delete(f"/api/v1/sa-keys/{created[0][0]}")
            ).status_code == 200

        async with SessionLocal() as session:
            assert await session.get(SAKey, created[0][0]) is None
            assignment = await session.get(SAKeyAssignment, hostname)
            assert str(assignment.key_id) == assigned_id
            for key_id, body in created[1:]:
                row = await session.get(SAKey, key_id)
                assert row is not None
                assert sa_key_vault.read_bytes(storage.sa_key_path(key_id)) == body
    finally:
        async with SessionLocal() as session:
            assignment = await session.get(SAKeyAssignment, hostname)
            if assignment is not None:
                await session.delete(assignment)
                await session.flush()
            rows = (
                await session.execute(
                    select(SAKey).where(SAKey.project_id.like(f"{prefix}%"))
                )
            ).scalars().all()
            for row in rows:
                await session.delete(row)
            await session.commit()
