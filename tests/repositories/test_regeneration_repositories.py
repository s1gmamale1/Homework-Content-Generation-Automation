"""Unit tests for ``app/repositories/regeneration_sources.py``.

The REAL function bodies run; only the ``session.execute`` / ``session.scalar``
boundary is faked (the pattern ``tests/repositories/test_cost.py`` uses). Two
kinds of assertion, both needed:

* **structural** — the compiled statement carries the predicate that makes the
  query authoritative (``revision_of_job_id IS NULL``, ``status='published'``,
  ``ORDER BY publication_version DESC``, ``FOR UPDATE``). A fake session cannot
  filter for us, so a dropped WHERE clause would otherwise be invisible here;
* **behavioural** — what the function returns for a given row set.

The semantics these predicates BUY (which row actually comes back, whether the
lock really blocks a second transaction) are proven against a real Postgres in
``tests/integration/test_regeneration_source_and_version_queries.py``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql


def _sql(stmt: Any) -> str:
    """Compiled Postgres SQL text for a statement the fake session captured."""
    return str(stmt.compile(dialect=postgresql.dialect()))


def _params(stmt: Any) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


class _Result:
    def __init__(self, rows: list, scalar_value: Any = None):
        self._rows = rows
        self._scalar = scalar_value

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    """Captures every statement and replays canned results in call order."""

    def __init__(self, *, execute_results: list | None = None, scalars: list | None = None):
        self.statements: list = []
        self._execute_results = list(execute_results or [])
        self._scalars = list(scalars or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        rows = self._execute_results.pop(0) if self._execute_results else []
        return _Result(rows)

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return self._scalars.pop(0) if self._scalars else None


@pytest.mark.asyncio
async def test_canary_statuses_locks_only_canary_rows_for_the_gate():
    from app.repositories import regeneration_targets as repo

    campaign_id = uuid.uuid4()
    session = _FakeSession(execute_results=[["awaiting_canary_approval"]])
    statuses = await repo.canary_statuses_for_campaign(
        session, campaign_id, for_update=True
    )

    assert statuses == ["awaiting_canary_approval"]
    sql = _sql(session.statements[0])
    assert "regeneration_targets.campaign_id = " in sql
    assert "regeneration_targets.is_canary IS true" in sql
    assert "FOR UPDATE" in sql


# ─────────────────────── latest_v1_source_job ────────────────────────


@pytest.mark.asyncio
async def test_latest_v1_source_job_excludes_revisions_and_non_homework_kinds():
    """A V1 source is a DONE, kind='homework', NON-revision job for the exact
    (toc_entry_id, output_language) lineage, newest first."""
    from app.repositories import regeneration_sources as repo

    job = object()
    session = _FakeSession(scalars=[job])
    got = await repo.latest_v1_source_job(
        session, toc_entry_id=uuid.uuid4(), output_language="uz"
    )

    assert got is job
    sql = _sql(session.statements[0])
    assert "homework_jobs.revision_of_job_id IS NULL" in sql
    assert "homework_jobs.kind = " in sql
    assert "homework_jobs.status = " in sql
    assert "homework_jobs.output_language = " in sql
    assert "ORDER BY homework_jobs.created_at DESC" in sql
    assert "LIMIT" in sql
    params = _params(session.statements[0])
    assert "homework" in params.values()
    assert "done" in params.values()


@pytest.mark.asyncio
async def test_latest_v1_source_job_returns_none_when_lineage_has_no_job():
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(scalars=[None])
    got = await repo.latest_v1_source_job(
        session, toc_entry_id=uuid.uuid4(), output_language="ru"
    )
    assert got is None


# ────────────────────── latest_published_target ──────────────────────


@pytest.mark.asyncio
async def test_latest_published_target_takes_the_highest_published_version():
    """V3+ sources come from the highest SUCCESSFULLY published version — an
    abandoned or still-publishing target must never be picked."""
    from app.repositories import regeneration_sources as repo

    target = object()
    session = _FakeSession(scalars=[target])
    got = await repo.latest_published_target(
        session, toc_entry_id=uuid.uuid4(), output_language="uz"
    )

    assert got is target
    sql = _sql(session.statements[0])
    assert "regeneration_targets.status = " in sql
    assert "published" in _params(session.statements[0]).values()
    assert "regeneration_targets.publication_version IS NOT NULL" in sql
    assert "ORDER BY regeneration_targets.publication_version DESC" in sql
    assert "FOR UPDATE" not in sql


@pytest.mark.asyncio
async def test_latest_published_target_can_take_a_row_lock():
    """Version allocation reads-then-writes, so it needs the locked variant."""
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(scalars=[None])
    await repo.latest_published_target(
        session, toc_entry_id=uuid.uuid4(), output_language="uz", for_update=True
    )
    assert "FOR UPDATE" in _sql(session.statements[0])


# ─────────────────────── next_expected_version ───────────────────────


@pytest.mark.asyncio
async def test_next_expected_version_is_two_when_no_version_was_ever_consumed():
    """Logical V1 is the existing Homework page and owns no target row, so the
    first number regeneration may allocate is 2."""
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(scalars=[None])
    assert (
        await repo.next_expected_version(
            session, toc_entry_id=uuid.uuid4(), output_language="uz"
        )
        == 2
    )


@pytest.mark.asyncio
async def test_next_expected_version_counts_every_consumed_version_not_just_published():
    """A version is consumed the moment publication is RELEASED, even if
    delivery later failed — so the max is taken over publication_version IS NOT
    NULL, never over status='published'."""
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(scalars=[4])
    got = await repo.next_expected_version(
        session, toc_entry_id=uuid.uuid4(), output_language="uz"
    )

    assert got == 5
    sql = _sql(session.statements[0])
    assert "max(regeneration_targets.publication_version)" in sql
    assert "regeneration_targets.publication_version IS NOT NULL" in sql
    assert "regeneration_targets.status" not in sql


@pytest.mark.asyncio
async def test_next_expected_version_is_scoped_to_one_language():
    """UZ V2 and RU V2 are independent publications (the unique index is per
    (toc_entry_id, output_language, publication_version))."""
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(scalars=[2])
    await repo.next_expected_version(
        session, toc_entry_id=uuid.uuid4(), output_language="ru"
    )
    sql = _sql(session.statements[0])
    assert "regeneration_targets.output_language = " in sql
    assert "ru" in _params(session.statements[0]).values()


# ───────────────────────────── lock_lineage ──────────────────────────


@pytest.mark.asyncio
async def test_lock_lineage_locks_every_target_row_of_the_lineage():
    """Two campaigns computing `next_expected_version` concurrently would both
    read the same max; the lineage lock is what serialises them."""
    from app.repositories import regeneration_sources as repo

    rows = [object(), object()]
    session = _FakeSession(execute_results=[rows])
    got = await repo.lock_lineage(
        session, toc_entry_id=uuid.uuid4(), output_language="uz"
    )

    assert got == rows
    sql = _sql(session.statements[0])
    assert "FOR UPDATE" in sql
    # Only the WHERE clause — an ORM entity select names every column in its
    # select list, so asserting over the whole statement would prove nothing.
    where = sql.split("WHERE", 1)[1]
    assert "regeneration_targets.toc_entry_id = " in where
    assert "regeneration_targets.output_language = " in where
    # Every row of the lineage — terminal ones included: a published target is
    # exactly the row that pins the consumed version.
    assert "terminal_at" not in where
    assert "regeneration_targets.status" not in where


# ─────────────────────────── candidate_lineages ──────────────────────


@pytest.mark.asyncio
async def test_candidate_lineages_applies_only_the_filters_it_was_given():
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(execute_results=[[]])
    await repo.candidate_lineages(
        session, book_ids=None, toc_entry_ids=None, output_languages=None
    )
    sql = _sql(session.statements[0])
    assert "IN (" not in sql
    # Even with no selection filter, only completed student homework counts.
    assert "homework_jobs.status = " in sql
    assert "homework_jobs.kind = " in sql


@pytest.mark.asyncio
async def test_candidate_lineages_applies_the_discovery_workload_limit():
    from app.repositories import regeneration_sources as repo

    session = _FakeSession(execute_results=[[]])
    await repo.candidate_lineages(
        session,
        book_ids=[uuid.uuid4()],
        toc_entry_ids=None,
        output_languages=None,
        limit=1001,
    )

    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    assert "LIMIT" in str(compiled)
    assert 1001 in compiled.params.values()


@pytest.mark.asyncio
async def test_candidate_lineages_filters_by_book_toc_and_language():
    from app.repositories import regeneration_sources as repo

    book_id, toc_id = uuid.uuid4(), uuid.uuid4()
    session = _FakeSession(execute_results=[[]])
    await repo.candidate_lineages(
        session,
        book_ids=[book_id],
        toc_entry_ids=[toc_id],
        output_languages=["ru"],
    )
    sql = _sql(session.statements[0])
    assert "homework_jobs.book_id IN (" in sql
    assert "homework_jobs.toc_entry_id IN (" in sql
    assert "homework_jobs.output_language IN (" in sql
    values = list(_params(session.statements[0]).values())
    assert book_id in values or [book_id] in values
    assert toc_id in values or [toc_id] in values


@pytest.mark.asyncio
async def test_candidate_lineages_returns_one_row_per_lesson_and_language():
    """The discovery unit is the LINEAGE (toc_entry_id, output_language) — two
    done jobs for the same lesson+language are one candidate, and a UZ and an
    RU job for the same lesson are two."""
    from app.repositories import regeneration_sources as repo

    toc_id, book_id = uuid.uuid4(), uuid.uuid4()
    rows = [
        (toc_id, "uz", book_id, "7", "b.pdf", "1", "L1", "C1", 4, None, 3),
        (toc_id, "ru", book_id, "7", "b.pdf", "1", "L1", "C1", 4, "pg", 3),
    ]
    session = _FakeSession(execute_results=[rows])
    got = await repo.candidate_lineages(
        session, book_ids=None, toc_entry_ids=None, output_languages=None
    )

    assert [(c.toc_entry_id, c.output_language) for c in got] == [
        (toc_id, "uz"),
        (toc_id, "ru"),
    ]
    assert got[0].grade == "7"
    assert got[0].book_filename == "b.pdf"
    assert got[0].section_number == "1"
    assert got[0].section_title == "L1"
    assert got[0].chapter_title == "C1"
    assert got[0].page_start == 4
    assert got[0].notion_lesson_page_id is None
    assert got[0].order_index == 3
    assert got[1].notion_lesson_page_id == "pg"
    sql = _sql(session.statements[0])
    assert "DISTINCT" in sql
    # `homework_jobs.subject` is job-varying (a book's subject is editable), so
    # it must never re-enter the DISTINCT key: it would split one lineage into
    # two candidates. Proven on real rows in the integration file's
    # `test_a_corrected_book_subject_does_not_split_one_lineage_in_two`.
    assert "homework_jobs.subject" not in sql


# ───────────────────────── phase_rows_for_jobs ───────────────────────


@pytest.mark.asyncio
async def test_phase_rows_for_jobs_groups_by_job_and_skips_the_query_when_empty():
    from app.repositories import regeneration_sources as repo

    session = _FakeSession()
    assert await repo.phase_rows_for_jobs(session, []) == {}
    assert session.statements == [], "an empty id list must not hit the database"

    job_a, job_b = uuid.uuid4(), uuid.uuid4()

    class _Row:
        def __init__(self, job_id, name):
            self.job_id = job_id
            self.phase_name = name

    rows = [_Row(job_a, "extract"), _Row(job_b, "flashcards"), _Row(job_a, "reflection")]
    session = _FakeSession(execute_results=[rows])
    grouped = await repo.phase_rows_for_jobs(session, [job_a, job_b])

    assert sorted(r.phase_name for r in grouped[job_a]) == ["extract", "reflection"]
    assert [r.phase_name for r in grouped[job_b]] == ["flashcards"]
    assert "phase_outputs.job_id IN (" in _sql(session.statements[0])


# ──────────────────── lineage_targets_missing_source ─────────────────


@pytest.mark.asyncio
async def test_lineage_targets_missing_source_selects_on_the_null_predicate():
    """Discovery must refuse a lineage on `source_job_id IS NULL` SPECIFICALLY
    (a child-first purge nulls the link and leaves the reporting row behind),
    so the query asks that exact question and returns the offending ids."""
    from app.repositories import regeneration_sources as repo

    ids = [uuid.uuid4(), uuid.uuid4()]
    session = _FakeSession(execute_results=[ids])
    got = await repo.lineage_targets_missing_source(
        session, toc_entry_id=uuid.uuid4(), output_language="uz"
    )

    assert got == ids
    sql = _sql(session.statements[0])
    assert "regeneration_targets.source_job_id IS NULL" in sql
    assert "regeneration_targets.toc_entry_id = " in sql
    assert "regeneration_targets.output_language = " in sql


# ═══════════════ ordinary-Fleet cost isolation (app/repositories/cost.py) ══
#
# A revision job is NOT Fleet work: it has no batch, it re-runs a lesson the
# operator already paid for on purpose, and its spend belongs to its campaign.
# The never-pay-twice warning ("this section already cost $X") must therefore
# never look at one, while the campaign's own actual-cost read must look at
# nothing else. These two tests are the pair that proves both directions.


class _RevisionJobStub:
    def __init__(self, *, revision_of_job_id=None, created_at_rank=0, cost_row=None):
        self.id = uuid.uuid4()
        self.revision_of_job_id = revision_of_job_id
        self.created_at_rank = created_at_rank
        self.cost_row = cost_row


class _SectionCostSession:
    """Two-query fake for ``section_prior_api_cost`` that HONOURS the job
    lookup's WHERE clause: if the statement carries the revision exclusion, the
    revision job is dropped exactly as Postgres would drop it. Removing that
    clause from cost.py therefore changes what this fake returns."""

    def __init__(self, jobs: list):
        self._jobs = jobs
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        result = MagicMock()
        if len(self.statements) == 1:
            sql = _sql(stmt)
            candidates = list(self._jobs)
            if "homework_jobs.revision_of_job_id IS NULL" in sql:
                candidates = [j for j in candidates if j.revision_of_job_id is None]
            candidates.sort(key=lambda j: j.created_at_rank, reverse=True)
            chosen = candidates[0] if candidates else None
            self._chosen = chosen
            result.scalar_one_or_none.return_value = chosen
        else:
            rows = [self._chosen.cost_row] if self._chosen.cost_row else []
            result.scalars.return_value.all.return_value = rows
        return result


def _usage_row(*, provider="gemini", model="gemini-3.5-flash", prompt=0, output=0):
    row = MagicMock()
    row.provider = provider
    row.model_name = model
    row.auth_mode = "api"
    row.prompt_tokens = prompt
    row.output_tokens = output
    row.cached_tokens = 0
    row.cache_creation_tokens = 0
    row.total_tokens = prompt + output
    return row


@pytest.mark.asyncio
async def test_section_prior_api_cost_never_reports_a_revisions_spend():
    """The newest job for the section is a REVISION. Without the exclusion the
    Fleet rebill warning would quote the regeneration's cost as the section's
    prior cost — and a re-launch would be waved through or blocked on a number
    that has nothing to do with ordinary generation."""
    from app.repositories.cost import section_prior_api_cost

    revision = _RevisionJobStub(
        revision_of_job_id=uuid.uuid4(),
        created_at_rank=2,  # newest
        # gemini-3.5-flash: 1M output × $9.00/M = $9.00
        cost_row=_usage_row(output=1_000_000),
    )
    ordinary = _RevisionJobStub(
        created_at_rank=1,
        # 1M prompt × $1.50/M = $1.50
        cost_row=_usage_row(prompt=1_000_000),
    )
    session = _SectionCostSession([revision, ordinary])

    cost, had_done = await section_prior_api_cost(
        session, uuid.uuid4(), uuid.uuid4(), "api"
    )

    assert had_done is True
    assert cost == pytest.approx(1.50, rel=1e-6), "the ordinary job's cost, not the revision's"
    assert "homework_jobs.revision_of_job_id IS NULL" in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_a_section_whose_only_job_is_a_revision_has_no_prior_fleet_cost():
    """had_done=False, not $0-with-True: an ordinary launch of this section has
    never happened, so the never-pay-twice gate must not claim it did."""
    from app.repositories.cost import section_prior_api_cost

    session = _SectionCostSession(
        [
            _RevisionJobStub(
                revision_of_job_id=uuid.uuid4(),
                cost_row=_usage_row(prompt=1_000_000),
            )
        ]
    )
    cost, had_done = await section_prior_api_cost(
        session, uuid.uuid4(), uuid.uuid4(), "api"
    )
    assert (cost, had_done) == (0.0, False)


@pytest.mark.asyncio
async def test_campaign_actual_api_cost_counts_revision_usage():
    """The other direction: a campaign's real spend is exactly the api usage of
    its own revision jobs (spec §12) — the rows Fleet's prior-cost read skips."""
    from app.repositories.cost import campaign_actual_api_cost_usd

    session = _FakeSession(
        execute_results=[[_usage_row(prompt=1_000_000), _usage_row(output=1_000_000)]]
    )
    cost = await campaign_actual_api_cost_usd(session, uuid.uuid4())

    # 1M prompt × $1.50/M + 1M output × $9.00/M
    assert cost == pytest.approx(10.50, rel=1e-6)
    sql = _sql(session.statements[0])
    assert "homework_jobs.revision_of_job_id IS NOT NULL" in sql
    assert "regeneration_targets.campaign_id = " in sql
    assert "agent_usages.auth_mode = " in sql


@pytest.mark.asyncio
async def test_fleet_daily_spend_still_includes_regeneration():
    """Regeneration spends REAL money on the same credential, so the fleet-wide
    daily cap must keep counting it. Excluding revisions here would let a
    campaign run the fleet past its cap invisibly."""
    from datetime import datetime, timezone

    from app.repositories.cost import fleet_api_cost_usd

    session = _FakeSession(execute_results=[[_usage_row(prompt=1_000_000)]])
    cost = await fleet_api_cost_usd(session, datetime(2026, 8, 1, tzinfo=timezone.utc))

    assert cost == pytest.approx(1.50, rel=1e-6)
    assert "revision_of_job_id" not in _sql(session.statements[0])


def test_batch_cost_needs_no_revision_filter_because_a_revision_has_no_batch():
    """`batch_api_cost_usd` is scoped by batch_id and a revision may not have
    one — enforced by the database, not by a convention a query could forget."""
    from app.models.homework_job import HomeworkJob

    names = {
        c.name for c in HomeworkJob.__table__.constraints if c.name is not None
    }
    assert "ck_homework_jobs_revision_no_batch" in names
