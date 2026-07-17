"""Autouse fixture for every test under ``tests/api/``.

Book-scoped advisory locks (BE-02 task 3, ``app.repositories.books``
``lock_book_shared``/``lock_book_exclusive``) are real ``pg_advisory_xact_lock``
calls issued through the request's SQLAlchemy session. Every route test in
this directory drives its endpoint through a fake/``MagicMock`` session (the
repo functions that touch the DB are mocked; ``get_session`` itself is either
left un-overridden — the session is never actually used — or overridden with
a bare ``MagicMock`` whose ``execute`` isn't an ``AsyncMock``), so a REAL call
to either lock helper would try to ``await`` a non-awaitable ``MagicMock`` (or
open a connection to the sentinel test ``DATABASE_URL``, which doesn't exist)
and blow up every one of these otherwise-unrelated tests.

The five activation call sites (``/generate``, job retry, batch launch, batch
resume, TOC retry) and the delete route all take one of these locks now.
Patch both helpers to inert no-ops here so existing mocked tests keep
exercising their own guard-ordering assertions unchanged. Tests that want to
prove the LOCK itself (real blocking, real winner-conditional outcomes) live
in ``tests/integration/test_book_delete_race.py`` against a real Postgres —
mocking it away here is deliberate, not a gap: this directory's tests were
never meant to (and mostly can't) exercise real Postgres semantics.

The three "read -> lock -> re-read" routes (job retry, batch resume, TOC
retry) also call ``session.expire(obj)`` on the pre-lock object right before
re-fetching it, to defeat SQLAlchemy's identity-map short-circuit (``Session.
get()`` silently hands back the SAME in-memory object on a second call unless
it's been expired — proven for real, and necessary, in
``tests/integration/test_book_delete_race.py``). These tests pass plain
``SimpleNamespace`` stand-ins through a mocked repo layer, which
``AsyncSession.expire()`` rejects outright (``UnmappedInstanceError`` — the
object was never loaded via this session). Made tolerant to exactly that
error here too (any other exception from ``expire()`` is a real bug and
must still surface).
"""
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import UnmappedInstanceError


@pytest.fixture(autouse=True)
def _noop_book_locks(monkeypatch):
    import app.repositories.books as books_repo

    monkeypatch.setattr(books_repo, "lock_book_shared", AsyncMock())
    monkeypatch.setattr(books_repo, "lock_book_exclusive", AsyncMock())

    real_expire = AsyncSession.expire

    def _expire_tolerant(self, instance, *args, **kwargs):
        try:
            return real_expire(self, instance, *args, **kwargs)
        except UnmappedInstanceError:
            return None

    monkeypatch.setattr(AsyncSession, "expire", _expire_tolerant)
    yield
