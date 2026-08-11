"""The RUN_DB_INTEGRATION guard in tests/conftest.py must refuse a production
target. Follow-up to the #133 gate.

The real-DB tests CREATE books/batches/jobs against whatever `DATABASE_URL` is
set, so an operator env plus `RUN_DB_INTEGRATION=1` used to seed production
silently. These tests exercise the guard function directly — the guard itself
runs at conftest import, which is far too early to assert against.
"""
import pytest

from tests.conftest import _guard_db_integration_target


def _run(monkeypatch, *, url: str, flag: str = "1"):
    monkeypatch.setenv("RUN_DB_INTEGRATION", flag)
    monkeypatch.setenv("DATABASE_URL", url)
    _guard_db_integration_target()


def test_refuses_the_production_database_by_name(monkeypatch):
    with pytest.raises(RuntimeError, match="PRODUCTION"):
        _run(monkeypatch, url="postgresql+asyncpg://edu:pw@127.0.0.1:5432/edu_copy")


def test_refuses_a_non_local_host(monkeypatch):
    """The worktree trap: a derived URL that walks up to the parent .env aims at
    the remote head. Local name, remote host — still refused."""
    with pytest.raises(RuntimeError, match="non-local host"):
        _run(monkeypatch, url="postgresql+asyncpg://edu:pw@192.168.1.80:5432/edu_scratch")


def test_allows_a_local_scratch_database(monkeypatch):
    _run(monkeypatch, url="postgresql+asyncpg://edu:pw@127.0.0.1:5432/edu_scratch_x")


def test_allows_a_normal_local_dev_database(monkeypatch):
    """Denylist, not allowlist — `edu_homework` on localhost is legitimate and
    must keep working, or this guard just breaks people's workflows."""
    _run(monkeypatch, url="postgresql+asyncpg://edu:edu@localhost:5433/edu_homework")


def test_is_inert_when_the_flag_is_off(monkeypatch):
    """Without RUN_DB_INTEGRATION the tests skip anyway and nothing writes, so
    the guard must not fire — otherwise the canonical suite bar breaks."""
    _run(monkeypatch, url="postgresql+asyncpg://edu:pw@127.0.0.1:5432/edu_copy", flag="0")


def test_is_inert_on_an_unparseable_url(monkeypatch):
    """Fail-open on garbage: a URL we cannot parse is not evidence of danger,
    and raising here would block runs for an unrelated reason."""
    _run(monkeypatch, url="not-a-url")
