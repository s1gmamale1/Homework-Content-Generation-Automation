"""Executable startup security ordering for the head and standalone worker."""

from __future__ import annotations

import os
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import main
from app.config import settings
from app.services import operator_auth, sa_key_vault
from app.services import worker as worker_module


STRONG_TOKEN = "F7a9Jm2_Rq6cV8xW1sK4nP0dZ5uH3yTbG9eL"


@asynccontextmanager
async def _session_context(session, calls: list[str]):
    calls.append("db")
    yield session


def _patch_head_after_security(monkeypatch, calls: list[str]) -> None:
    session = object()
    monkeypatch.setattr(
        main,
        "SessionLocal",
        lambda: _session_context(session, calls),
    )
    monkeypatch.setattr(main, "load_prompts", lambda: calls.append("prompts"))

    async def reconcile(passed_session):
        assert passed_session is session
        calls.append("jobs")

    async def start_listener():
        calls.append("listener")

    async def stop_listener():
        calls.append("listener-stop")

    monkeypatch.setattr(main, "_reconcile_on_startup", reconcile)
    monkeypatch.setattr(main.events_bus, "start_listener", start_listener)
    monkeypatch.setattr(main.events_bus, "stop_listener", stop_listener)
    monkeypatch.setattr(
        main,
        "build_worker_from_settings",
        lambda: calls.append("worker"),
    )
    monkeypatch.setattr(settings, "worker_concurrency", 0)
    monkeypatch.setattr(main.code_version, "CODE_VERSION", None)


@pytest.mark.asyncio
async def test_head_rejects_bad_auth_before_prompts_db_listener_or_worker(monkeypatch):
    """Moving auth below any startup seam would make this test observe a side effect."""
    monkeypatch.setattr(settings, "auth_token", "123")
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    _patch_head_after_security(monkeypatch, calls)

    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        async with main.app.router.lifespan_context(main.app):
            pass

    assert calls == []


@pytest.mark.asyncio
async def test_standalone_rejects_before_logging_prompts_worker_or_db(monkeypatch):
    """Moving auth below local imports/building a worker would expose a side effect."""
    from app import log as app_log
    from app.services import prompts

    monkeypatch.setattr(settings, "auth_token", "123")
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    monkeypatch.setattr(app_log, "configure", lambda: calls.append("logging"))
    monkeypatch.setattr(prompts, "load_all", lambda: calls.append("prompts"))
    monkeypatch.setattr(
        worker_module,
        "build_worker_from_settings",
        lambda: calls.append("worker"),
    )
    monkeypatch.setattr(
        worker_module,
        "SessionLocal",
        lambda: calls.append("db"),
    )

    with pytest.raises(operator_auth.OperatorAuthConfigurationError):
        await worker_module.run_standalone()

    assert calls == []


@pytest.mark.asyncio
async def test_head_security_preflight_and_inventory_precede_later_startup(monkeypatch):
    """Omitting or reordering any auth/vault/inventory step breaks the literal trace."""
    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    _patch_head_after_security(monkeypatch, calls)

    monkeypatch.setattr(
        main,
        "operator_auth",
        SimpleNamespace(
            require_startup_auth=lambda *_args, **_kwargs: calls.append("auth")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "sa_key_vault",
        SimpleNamespace(
            harden_vault=lambda: calls.append("harden"),
            reconcile_delete_quarantines=lambda expected: calls.append(
                ("quarantines", expected)
            ),
            verify_uuid_inventory=lambda expected: calls.append(
                ("inventory", expected)
            ),
        ),
        raising=False,
    )

    async def uuid_inventory(session):
        calls.append("inventory-db")
        return {"00000000-0000-0000-0000-000000000001.json": "a" * 64}

    monkeypatch.setattr(
        main,
        "sa_keys_repo",
        SimpleNamespace(uuid_hash_inventory=uuid_inventory),
        raising=False,
    )

    async with main.app.router.lifespan_context(main.app):
        calls.append("yield")

    expected = {"00000000-0000-0000-0000-000000000001.json": "a" * 64}
    assert calls == [
        "auth",
        "harden",
        "prompts",
        "db",
        "inventory-db",
        ("quarantines", expected),
        ("inventory", expected),
        "jobs",
        "listener",
        "yield",
        "listener-stop",
    ]


@pytest.mark.asyncio
async def test_head_vault_failure_precedes_prompts_and_database(monkeypatch):
    """A fail-open vault preflight would let prompts or the DB be touched."""
    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    _patch_head_after_security(monkeypatch, calls)
    monkeypatch.setattr(
        main,
        "sa_key_vault",
        SimpleNamespace(
            harden_vault=lambda: (_ for _ in ()).throw(
                sa_key_vault.SAKeyVaultError("private/path/detail")
            )
        ),
        raising=False,
    )

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        async with main.app.router.lifespan_context(main.app):
            pass

    assert calls == []


@pytest.mark.asyncio
async def test_head_database_inventory_uncertainty_stops_before_reconcile(monkeypatch):
    """Treating an inventory read failure as empty would discard quarantines."""
    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    _patch_head_after_security(monkeypatch, calls)
    monkeypatch.setattr(
        main,
        "sa_key_vault",
        SimpleNamespace(harden_vault=lambda: calls.append("harden")),
    )

    async def uncertain(_session):
        calls.append("inventory-db")
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        main,
        "sa_keys_repo",
        SimpleNamespace(uuid_hash_inventory=uncertain),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        async with main.app.router.lifespan_context(main.app):
            pass

    assert calls == ["harden", "prompts", "db", "inventory-db"]


@pytest.mark.asyncio
async def test_standalone_orders_auth_vault_before_logging_prompts_and_worker(
    monkeypatch,
):
    """A missing worker preflight would omit auth/harden from the trace."""
    from app import log as app_log
    from app.services import prompts

    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    monkeypatch.setattr(app_log, "configure", lambda: calls.append("logging"))
    monkeypatch.setattr(prompts, "load_all", lambda: calls.append("prompts"))
    monkeypatch.setattr(
        worker_module,
        "operator_auth",
        SimpleNamespace(
            require_startup_auth=lambda *_args, **_kwargs: calls.append("auth")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "sa_key_vault",
        SimpleNamespace(harden_vault=lambda: calls.append("harden")),
    )
    monkeypatch.setattr(
        worker_module,
        "_rebind_capabilities",
        lambda: calls.append("auth-fingerprint"),
    )

    class FakeWorker:
        def stop(self):
            calls.append("stop")

        async def run(self):
            calls.append("run")

    monkeypatch.setattr(
        worker_module,
        "build_worker_from_settings",
        lambda: calls.append("worker") or FakeWorker(),
    )

    class FakeLoop:
        def add_signal_handler(self, _signal, _callback):
            calls.append("signal")

    monkeypatch.setattr(worker_module.asyncio, "get_event_loop", lambda: FakeLoop())

    await worker_module.run_standalone()

    assert calls[:6] == [
        "auth",
        "harden",
        "auth-fingerprint",
        "logging",
        "prompts",
        "worker",
    ]
    assert calls[-1] == "run"


@pytest.mark.asyncio
async def test_standalone_vault_failure_precedes_logging_prompts_and_worker(monkeypatch):
    """Catching a worker vault refusal would allow an unsafe process to continue."""
    from app import log as app_log
    from app.services import prompts

    monkeypatch.setattr(settings, "auth_token", STRONG_TOKEN)
    monkeypatch.setattr(settings, "allow_insecure_local_auth", False)
    calls: list[str] = []
    monkeypatch.setattr(app_log, "configure", lambda: calls.append("logging"))
    monkeypatch.setattr(prompts, "load_all", lambda: calls.append("prompts"))
    monkeypatch.setattr(
        worker_module,
        "build_worker_from_settings",
        lambda: calls.append("worker"),
    )
    monkeypatch.setattr(
        worker_module.sa_key_vault,
        "harden_vault",
        lambda: (_ for _ in ()).throw(sa_key_vault.SAKeyVaultError("refused")),
    )

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        await worker_module.run_standalone()

    assert calls == []


@pytest.mark.asyncio
async def test_local_dev_head_reaches_vault_but_strict_sa_auth_stays_unavailable(
    monkeypatch,
):
    """The local startup escape hatch must not open the credential-vault API."""
    from fastapi import HTTPException

    from app.auth import get_current_user_strict

    monkeypatch.setattr(settings, "auth_token", "")
    monkeypatch.setattr(settings, "allow_insecure_local_auth", True)
    calls: list[str] = []
    _patch_head_after_security(monkeypatch, calls)

    def fail_after_auth():
        calls.append("harden")
        raise sa_key_vault.SAKeyVaultError("stop after auth")

    monkeypatch.setattr(
        main,
        "sa_key_vault",
        SimpleNamespace(harden_vault=fail_after_auth),
        raising=False,
    )

    with pytest.raises(sa_key_vault.SAKeyVaultError):
        async with main.app.router.lifespan_context(main.app):
            pass
    assert calls == ["harden"]

    with pytest.raises(HTTPException) as caught:
        await get_current_user_strict(authorization=None, token=None)
    assert caught.value.status_code == 503


@pytest.mark.parametrize("module_name", ["main", "app.services.worker"])
def test_import_is_safe_without_auth_configuration(module_name):
    """Adding auth validation at import time makes this subprocess fail."""
    env = dict(os.environ)
    env["AUTH_TOKEN"] = ""
    env["ALLOW_INSECURE_LOCAL_AUTH"] = "false"
    completed = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
