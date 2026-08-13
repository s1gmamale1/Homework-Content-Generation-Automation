"""Guards on the read-only TOC audit script.

The script's value is that an operator can trust it: it must refuse to guess a
target database, it must be runnable as a plain query, and the repair it prints
must be the SAME rule the ingest guard applies — a report that disagreed with
the live guard would be worse than no report.
"""
from __future__ import annotations

import pytest

from scripts import audit_toc_page_ranges as script


def test_refuses_to_guess_the_target_database():
    with pytest.raises(script.PreflightError) as exc:
        script.preflight_database_url({})
    assert "DATABASE_URL must be set explicitly" in str(exc.value)


def test_explicit_database_url_passes_through():
    url = "postgresql+asyncpg://edu:edu@localhost:5433/edu_homework"
    assert script.preflight_database_url({"DATABASE_URL": url}) == url


def test_missing_database_url_is_one_clean_line_not_a_traceback(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert script.main([]) == 2

    assert "ERROR: DATABASE_URL must be set explicitly" in capsys.readouterr().err


def test_show_sql_needs_no_database(monkeypatch, capsys):
    """The audit doubles as a documented query — `--show-sql` must work with no
    DB configured at all."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert script.main(["--show-sql"]) == 0

    out = capsys.readouterr().out
    assert "page_end   <  t.page_start" in out
    assert "both_null" in out


def test_printed_repair_matches_the_ingest_guard_rule():
    """The script prints `page_end = page_start`; the guard writes the same.
    Pinned together so a change to one is a visible break in the other."""
    from types import SimpleNamespace

    from app.services.toc_ingest_audit import audit_page_ranges

    entry = SimpleNamespace(section_title="L", page_start=35, page_end=34)
    audit_page_ranges([entry])

    assert entry.page_end == entry.page_start
    assert "SET    page_end = page_start" in script.REPAIR_SQL_TEMPLATE
