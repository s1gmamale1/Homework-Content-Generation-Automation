"""Regression guard for the `delete_for_book` name-collision bug.

`toc_entries.py` imports SQLAlchemy's `delete` AND defines a public
`delete(session, toc_entry_id)` single-entry function that rebinds the module
name. `delete_for_book` must therefore use the aliased `sa_delete`, or its
`delete(TOCEntry)` resolves to the single-entry function and raises
`TypeError: delete() missing 1 required positional argument: 'toc_entry_id'`
at runtime (observed live: a successful 60-entry extraction crashed at persist).

This test executes the REAL `delete_for_book` body (no mock of the function
itself) so the collision can't regress. It needs no database.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.sql.dml import Delete

from app.repositories import toc_entries as toc_repo


@pytest.mark.asyncio
async def test_delete_for_book_emits_sqlalchemy_delete():
    captured: dict = {}

    async def fake_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.rowcount = 3
        return result

    session = MagicMock()
    session.execute = fake_execute

    removed = await toc_repo.delete_for_book(session, uuid4())

    # Returns the rowcount and — the regression — built a SQLAlchemy DELETE,
    # not the repo's single-entry delete() (which would have raised TypeError).
    assert removed == 3
    assert isinstance(captured["stmt"], Delete)
