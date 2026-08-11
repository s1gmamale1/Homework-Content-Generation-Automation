from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent_usage import AgentUsage
from app.models.batch import Batch
from app.models.book import Book
from app.models.homework_job import HomeworkJob
from app.models.job_lease_event import JobLeaseEvent
from app.models.toc_entry import TOCEntry
from scripts import fenced_lease_soak as soak


requires_db = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="set RUN_DB_INTEGRATION=1 with a scratch DATABASE_URL",
)


@pytest.fixture(scope="module")
def database_url() -> str:
    url = os.environ["DATABASE_URL"]
    soak.assert_scratch_database_url(url)
    return url


@pytest.fixture
async def seeded_scope(database_url):
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    book_id, toc_id, batch_id = uuid4(), uuid4(), uuid4()
    scoped_id, unscoped_id = uuid4(), uuid4()
    async with factory() as session:
        book = Book(
            id=book_id,
            subject="matematika",
            grade="5",
            original_filename="soak.pdf",
            content_sha256="a" * 64,
            file_size_bytes=3,
            status="toc_ready",
            source_language="uz",
        )
        toc = TOCEntry(
            id=toc_id,
            book_id=book_id,
            section_number="1",
            section_title="Soak",
            page_start=1,
            page_end=1,
            order_index=1,
        )
        batch = Batch(
            id=batch_id,
            book_id=book_id,
            subject="matematika",
            grade="5",
            provider="gemini",
            model="gemini-3.6-flash",
            transport="api",
            output_language="uz",
        )
        session.add(book)
        await session.flush()
        session.add_all([toc, batch])
        await session.flush()
        for job_id in (scoped_id, unscoped_id):
            session.add(HomeworkJob(
                id=job_id,
                book_id=book_id,
                toc_entry_id=toc_id,
                batch_id=batch_id,
                subject="matematika",
                status="failed",
                provider="gemini",
                model="gemini-3.6-flash",
                transport="api",
                output_language="uz",
                attempts=1,
                created_at=now,
                updated_at=now,
            ))
        await session.flush()
        session.add_all([
            AgentUsage(
                homework_job_id=scoped_id,
                provider="gemini",
                operation="phase.run",
                model_name="gemini-3.6-flash",
                auth_mode="api",
                prompt_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
            AgentUsage(
                homework_job_id=unscoped_id,
                provider="gemini",
                operation="phase.run",
                model_name="gemini-3.6-flash",
                auth_mode="api",
                prompt_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
            JobLeaseEvent(
                job_id=scoped_id,
                claim_token=uuid4(),
                event_type="claimed",
                created_at=now,
            ),
            JobLeaseEvent(
                job_id=unscoped_id,
                claim_token=uuid4(),
                event_type="claimed",
                created_at=now,
            ),
        ])
        await session.commit()
    scope = soak.SoakScope(
        run_id="integration-soak",
        since=now - timedelta(minutes=1),
        batch_ids=[batch_id],
        job_ids=[scoped_id],
        participant_hosts=["none"],
        target_running=1,
        expected_git_sha="fedcba9",
        expected_code_version=1001,
        expected_db_revision="0052_job_lease_fencing",
        worker_concurrency=1,
        agent_max_concurrency=1,
        credential_max_concurrent_gemini=1,
        credential_slot_wait_seconds=1,
        legacy_gemini_var_must_be_absent=True,
        structured_output_enabled=False,
        required_book_sha256={str(book_id): "a" * 64},
        forbidden_notion_mapping_keys=[],
        expected_models_by_operation_prefix={
            "phase.run": "gemini-3.6-flash",
            "lesson.extract": "gemini-3.5-flash-lite",
            "lesson.extract.coverage": "gemini-3.5-flash",
            "lesson.extract.verify": "gemini-3.5-flash-lite",
            "judge:": "gemini-3.5-flash",
            "solve:": "gemini-3.1-pro-preview",
        },
        approved_incremental_cost_usd="1",
        fleet_cost_limit_usd="2",
        db_preflight_connection_limit=90,
        db_hard_stop_connection_limit=99,
        heartbeat_max_age_seconds=60,
        attestation_max_age_seconds=300,
        settle_seconds=1,
    )
    try:
        yield scope, unscoped_id
    finally:
        async with factory() as session:
            await session.execute(delete(AgentUsage).where(AgentUsage.homework_job_id.in_([scoped_id, unscoped_id])))
            await session.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_([scoped_id, unscoped_id])))
            await session.execute(delete(HomeworkJob).where(HomeworkJob.id.in_([scoped_id, unscoped_id])))
            await session.execute(delete(Batch).where(Batch.id == batch_id))
            await session.execute(delete(TOCEntry).where(TOCEntry.id == toc_id))
            await session.execute(delete(Book).where(Book.id == book_id))
            await session.commit()
        await engine.dispose()


@pytest.fixture
async def stop_context(database_url, seeded_scope):
    scope, unscoped_id = seeded_scope
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    foreign_book_id, foreign_batch_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            Book(
                id=foreign_book_id,
                subject="matematika",
                grade="5",
                original_filename="soak-foreign.pdf",
                content_sha256="b" * 64,
                file_size_bytes=3,
                status="toc_ready",
                source_language="uz",
            )
        )
        await session.flush()
        session.add(
            Batch(
                id=foreign_batch_id,
                book_id=foreign_book_id,
                subject="matematika",
                grade="5",
                provider="gemini",
                model="gemini-3.6-flash",
                transport="api",
                output_language="uz",
            )
        )
        await session.execute(
            text(
                "update budget_state set api_paused_at=null, "
                "api_paused_reason=null where id=1"
            )
        )
        await session.execute(
            text(
                "update batches set paused_at=null, paused_reason=null "
                "where id=:batch_id"
            ),
            {"batch_id": scope.batch_ids[0]},
        )
        await session.commit()
    try:
        yield {
            "scope": scope,
            "unscoped_id": unscoped_id,
            "foreign_batch_id": foreign_batch_id,
            "factory": factory,
        }
    finally:
        async with factory() as session:
            await session.execute(
                text(
                    "update homework_jobs set batch_id=:batch_id "
                    "where id=:job_id"
                ),
                {"batch_id": scope.batch_ids[0], "job_id": scope.job_ids[0]},
            )
            await session.execute(
                text(
                    "update budget_state set api_paused_at=null, "
                    "api_paused_reason=null where id=1"
                )
            )
            await session.execute(
                text(
                    "update batches set paused_at=null, paused_reason=null "
                    "where id=:batch_id"
                ),
                {"batch_id": scope.batch_ids[0]},
            )
            await session.execute(delete(Batch).where(Batch.id == foreign_batch_id))
            await session.execute(delete(Book).where(Book.id == foreign_book_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@requires_db
async def test_collect_starts_a_read_only_transaction(database_url, seeded_scope):
    scope, _ = seeded_scope
    store = soak.SqlSoakReadStore(database_url)
    try:
        raw = await store.collect(scope)
    finally:
        await store.dispose()
    assert raw.scope_job_ids == scope.job_ids
    assert raw.transaction_read_only == "on"
    assert raw.db.idle_in_transaction_timeout_ms == 300_000


@pytest.mark.asyncio
@requires_db
async def test_read_store_cannot_write(database_url):
    store = soak.SqlSoakReadStore(database_url)
    try:
        async with store.read_connection() as conn:
            app_name = await conn.scalar(text("select current_setting('application_name')"))
            assert app_name.startswith("hcga-soak:")
            with pytest.raises(DBAPIError, match="read-only transaction"):
                await conn.execute(text("update homework_jobs set priority=priority"))
    finally:
        await store.dispose()


@pytest.mark.asyncio
@requires_db
async def test_collect_excludes_unscoped_usage_and_lease_events(database_url, seeded_scope):
    scope, unscoped_id = seeded_scope
    store = soak.SqlSoakReadStore(database_url)
    try:
        raw = await store.collect(scope)
    finally:
        await store.dispose()
    assert unscoped_id not in {row.job_id for row in raw.usages}
    assert unscoped_id not in {row.job_id for row in raw.lease_events}


@pytest.mark.asyncio
@requires_db
async def test_stop_mutates_only_exact_batches_and_budget_state(
    database_url, stop_context
):
    scope = stop_context["scope"]
    factory = stop_context["factory"]
    async with factory() as session:
        before = dict(
            (
                await session.execute(
                    text(
                        "select id, status from homework_jobs "
                        "where id = any(cast(:job_ids as uuid[])) order by id"
                    ),
                    {"job_ids": [scope.job_ids[0], stop_context["unscoped_id"]]},
                )
            ).all()
        )

    write_store = soak.SqlSoakWriteStore(database_url)
    try:
        receipt = await soak.GuardedStopper(write_store).pause(
            scope,
            soak.Finding(
                code="lease_lost",
                hard=True,
                hard_stop=True,
                stage_failure=True,
                message="lease lost",
            ),
        )
    finally:
        await write_store.dispose()

    async with factory() as session:
        after = dict(
            (
                await session.execute(
                    text(
                        "select id, status from homework_jobs "
                        "where id = any(cast(:job_ids as uuid[])) order by id"
                    ),
                    {"job_ids": [scope.job_ids[0], stop_context["unscoped_id"]]},
                )
            ).all()
        )
        unrelated_reason = await session.scalar(
            text("select paused_reason from batches where id=:batch_id"),
            {"batch_id": stop_context["foreign_batch_id"]},
        )
        fleet_reason = await session.scalar(
            text("select api_paused_reason from budget_state where id=1")
        )

    assert receipt.batches_paused == len(scope.batch_ids)
    assert unrelated_reason is None
    assert before == after
    assert fleet_reason == f"lease-soak-stop:{scope.run_id}"


@pytest.mark.asyncio
@requires_db
async def test_stop_rolls_back_everything_on_scope_drift(
    database_url, stop_context
):
    scope = stop_context["scope"]
    factory = stop_context["factory"]
    async with factory() as session:
        await session.execute(
            text("update homework_jobs set batch_id=:batch_id where id=:job_id"),
            {
                "batch_id": stop_context["foreign_batch_id"],
                "job_id": scope.job_ids[0],
            },
        )
        await session.commit()

    write_store = soak.SqlSoakWriteStore(database_url)
    try:
        with pytest.raises(soak.ScopeDrift):
            await soak.GuardedStopper(write_store).pause(
                scope,
                soak.Finding(
                    code="lease_lost",
                    hard=True,
                    hard_stop=True,
                    stage_failure=True,
                    message="lease lost",
                ),
            )
    finally:
        await write_store.dispose()

    async with factory() as session:
        batch_reason = await session.scalar(
            text("select paused_reason from batches where id=:batch_id"),
            {"batch_id": scope.batch_ids[0]},
        )
        fleet_reason = await session.scalar(
            text("select api_paused_reason from budget_state where id=1")
        )
    assert batch_reason is None
    assert fleet_reason is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://edu@127.0.0.1/edu_copy",
        "postgresql+asyncpg://edu@127.0.0.1/edu_homework",
        "postgresql+asyncpg://edu@127.0.0.1/postgres",
        "",
    ],
)
def test_seed_fixtures_refuse_non_scratch_database(url):
    with pytest.raises(RuntimeError, match="scratch database required"):
        soak.assert_scratch_database_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://edu@127.0.0.1/edu_scratch_leases",
        "postgresql+asyncpg://edu@127.0.0.1/controller_test",
    ],
)
def test_seed_fixtures_accept_explicit_scratch_database(url):
    soak.assert_scratch_database_url(url)
