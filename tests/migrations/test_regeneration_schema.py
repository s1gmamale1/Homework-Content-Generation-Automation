"""Real-DB: the regeneration migrations, upgraded and reverted for real.

0063 creates the campaign/target schema. It is the first trigger in the
repository, so that is inspected rather than assumed: function body,
timing/events, the refusal it enforces BEFORE campaign approval, the acceptance
AFTER approval, and the removal of BOTH the trigger and its function on
downgrade.

0064 adds the guided-wizard columns on top — the campaign-wide publication
version and the five reviewed-Notion-destination fields — plus the two named
CHECKs that police them.

Recipe: same as test_0061_credential_slot_index.py (subprocess alembic +
explicit DATABASE_URL).
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="needs a real Postgres"
)

_DB_URL = os.getenv("DATABASE_URL", "")
_PREV = "0062_cap_pause_provenance"
_TRIGGER = "trg_regeneration_targets_publication_gate"
_FUNCTION = "regeneration_target_publication_gate"

# ── 0064: campaign publication version + reviewed Notion destination ────────
_PREV_0064 = "0063_regeneration_campaigns"
_CAMPAIGN_VERSION_CHECK = "ck_regeneration_campaigns_publication_version"
_DESTINATION_CHECK = "ck_regeneration_targets_notion_parent_decision"
_DESTINATION_COLUMNS = (
    "notion_container_policy",
    "reviewed_notion_container_page_id",
    "notion_parent_policy",
    "reviewed_notion_lesson_page_id",
    "reviewed_notion_lesson_title",
)


def _run_alembic(cmd: list[str]) -> None:
    env = {**os.environ, "DATABASE_URL": _DB_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic"] + cmd,
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(cmd)} failed:\n{result.stdout}\n{result.stderr}"
        )


async def _has_table(engine, table: str) -> bool:
    async with engine.begin() as conn:
        return bool(
            await conn.scalar(
                text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
            )
        )


async def _has_column(engine, table: str, column: str) -> bool:
    async with engine.begin() as conn:
        got = await conn.scalar(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        )
    return got == column


async def _constraint_def(engine, name: str) -> str | None:
    async with engine.begin() as conn:
        return await conn.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=:n"
            ),
            {"n": name},
        )


async def _index_def(engine, name: str) -> str | None:
    async with engine.begin() as conn:
        return await conn.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname=:n"), {"n": name}
        )


async def _fk_delete_action(engine, table: str, constraint: str) -> str | None:
    """'r' = RESTRICT, 'c' = CASCADE, 'n' = SET NULL, 'a' = NO ACTION."""
    async with engine.begin() as conn:
        got = await conn.scalar(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname=:n AND conrelid = cast(:t AS regclass)"
            ),
            {"n": constraint, "t": table},
        )
    # pg's "char" column comes back as bytes over asyncpg.
    return got.decode() if isinstance(got, (bytes, bytearray)) else got


async def _seed_lesson(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A book + TOC entry + a normal source job to hang a target off."""
    book_id, toc_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO books (id, subject, original_filename, content_sha256,"
                " file_size_bytes, status, created_at, updated_at) VALUES "
                "(:id,'math-algebra','regen-0063.pdf',:sha,1,'toc_ready',now(),now())"
            ),
            {"id": book_id, "sha": uuid.uuid4().hex * 2},
        )
        await conn.execute(
            text(
                "INSERT INTO toc_entries (id, book_id, section_title, order_index,"
                " created_at, updated_at) VALUES (:id,:book,'L1',0,now(),now())"
            ),
            {"id": toc_id, "book": book_id},
        )
        await conn.execute(
            text(
                "INSERT INTO homework_jobs (id, book_id, toc_entry_id, subject, status,"
                " provider, transport, output_language, created_at, updated_at) VALUES "
                "(:id,:book,:toc,'math-algebra','done','gemini','api','uz',now(),now())"
            ),
            {"id": job_id, "book": book_id, "toc": toc_id},
        )
    return book_id, toc_id, job_id


async def _cleanup_lesson(engine, book_id, toc_id, job_id) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM homework_jobs WHERE id=:id"), {"id": job_id}
        )
        await conn.execute(text("DELETE FROM toc_entries WHERE id=:id"), {"id": toc_id})
        await conn.execute(text("DELETE FROM books WHERE id=:id"), {"id": book_id})


async def test_0063_creates_regeneration_schema_and_reverts_cleanly():
    engine = create_async_engine(_DB_URL)
    try:
        # ── RED baseline: nothing regeneration-shaped exists at 0062 ─────────
        _run_alembic(["downgrade", _PREV])
        assert not await _has_table(engine, "regeneration_campaigns")
        assert not await _has_table(engine, "regeneration_targets")
        assert not await _has_column(engine, "homework_jobs", "revision_of_job_id")
        assert not await _has_column(engine, "phase_outputs", "copied_from_phase_output_id")

        # ── upgrade ─────────────────────────────────────────────────────────
        _run_alembic(["upgrade", "head"])
        assert await _has_table(engine, "regeneration_campaigns")
        assert await _has_table(engine, "regeneration_targets")

        for column in (
            "status",
            "requested_phases",
            "excluded_phases",
            "selection_spec",
            "launch_contract",
            "refresh_extraction",
            "exclusion_acknowledged",
            "canary_size",
            "estimated_cost_low_usd",
            "estimated_cost_high_usd",
            "app_git_revision",
            "canary_launched_at",
            "approved_at",
            "rejected_at",
            "cancel_requested_at",
            "completed_at",
            "rejected_reason",
            "cancel_requested_reason",
        ):
            assert await _has_column(engine, "regeneration_campaigns", column), column

        for column in (
            "campaign_id",
            "toc_entry_id",
            "output_language",
            "source_job_id",
            "is_canary",
            "phase_plan",
            "status",
            "publication_released_at",
            "publication_version",
            "notion_page_id",
            "publication_claim_token",
            "publication_claimed_at",
            "publication_attempts",
            "publication_next_attempt_at",
            "publication_last_error",
            "terminal_at",
            "terminal_reason",
            "abandon_requested_at",
            "abandon_requested_reason",
        ):
            assert await _has_column(engine, "regeneration_targets", column), column

        assert await _has_column(engine, "homework_jobs", "revision_of_job_id")
        assert await _has_column(engine, "homework_jobs", "regeneration_target_id")
        # The THIRD homework_jobs column: a revision has batch_id NULL by
        # construction, so it has no batch row to resolve a session-limit
        # strategy from and must carry its own concrete one.
        assert await _has_column(engine, "homework_jobs", "session_limit_strategy")
        assert await _has_column(engine, "phase_outputs", "copied_from_phase_output_id")

        # ── named constraints ───────────────────────────────────────────────
        for name in (
            "ck_regeneration_campaigns_status",
            "ck_regeneration_targets_status",
            "ck_regeneration_targets_output_language",
            "ck_regeneration_targets_terminal_at",
            "ck_regeneration_targets_published_complete",
            "ck_regeneration_targets_publication_released",
            "ck_regeneration_targets_publication_attempts",
            "ck_homework_jobs_revision_pair",
            "ck_homework_jobs_revision_no_batch",
            "ck_homework_jobs_session_limit_strategy",
            "ck_homework_jobs_revision_session_limit_strategy",
            "uq_regeneration_targets_campaign_toc_language",
            "uq_homework_jobs_regeneration_target_id",
        ):
            assert await _constraint_def(engine, name) is not None, f"{name} missing"

        # The revision rule refuses 'inherit', not merely NULL: 'inherit'
        # re-resolves against the mutable fleet-wide default at run time, which
        # is precisely the no-op this column exists to close.
        revision_rule = await _constraint_def(
            engine, "ck_homework_jobs_revision_session_limit_strategy"
        )
        assert "'pause'" in revision_rule and "'switch'" in revision_rule
        assert "'inherit'" not in revision_rule
        assert "revision_of_job_id IS NULL" in revision_rule
        # ...while an ordinary job may still say 'inherit' (or nothing at all).
        general_rule = await _constraint_def(
            engine, "ck_homework_jobs_session_limit_strategy"
        )
        assert "'inherit'" in general_rule
        assert "session_limit_strategy IS NULL" in general_rule

        # ── partial unique indexes ──────────────────────────────────────────
        lineage = await _index_def(engine, "uq_regeneration_targets_active_lineage")
        assert lineage is not None and "UNIQUE" in lineage
        assert "terminal_at IS NULL" in lineage
        version = await _index_def(engine, "uq_regeneration_targets_publication_version")
        assert version is not None and "UNIQUE" in version
        assert "publication_version IS NOT NULL" in version

        # ── restrictive FKs (no implicit cascade may erase audit history) ────
        assert await _fk_delete_action(
            engine, "regeneration_targets", "fk_regeneration_targets_toc_entry_id"
        ) == "r"
        assert await _fk_delete_action(
            engine, "regeneration_targets", "fk_regeneration_targets_campaign_id"
        ) == "r"
        # The ONE deliberate non-restrictive key (spec §8.3): after a
        # child-first purge deletes the revision, the source may go and the
        # target survives as a reporting row with a null source link.
        assert await _fk_delete_action(
            engine, "regeneration_targets", "fk_regeneration_targets_source_job_id"
        ) == "n"
        assert await _fk_delete_action(
            engine, "homework_jobs", "fk_homework_jobs_revision_of_job_id"
        ) == "r"
        assert await _fk_delete_action(
            engine, "homework_jobs", "fk_homework_jobs_regeneration_target_id"
        ) == "r"
        assert await _fk_delete_action(
            engine, "phase_outputs", "fk_phase_outputs_copied_from_phase_output_id"
        ) == "r"

        # ── trigger: timing, events, level, and the function body ───────────
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT t.tgtype, t.tgenabled, p.proname, p.prosrc "
                        "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                        "WHERE t.tgname = :n AND t.tgrelid = 'regeneration_targets'::regclass"
                    ),
                    {"n": _TRIGGER},
                )
            ).one_or_none()
        assert row is not None, f"{_TRIGGER} missing"
        tgtype, tgenabled, proname, prosrc = row
        # bit 0 = ROW-level, bit 1 = BEFORE, bit 2 = INSERT, bit 4 = UPDATE.
        assert tgtype & 1, "trigger must be FOR EACH ROW"
        assert tgtype & 2, "trigger must be BEFORE (it must veto, not observe)"
        assert tgtype & 4, "trigger must fire on INSERT"
        assert tgtype & 16, "trigger must fire on UPDATE"
        assert not tgtype & 8, "trigger must not fire on DELETE"
        # "O" = enabled in origin/local mode (i.e. actually firing).
        assert (tgenabled.decode() if isinstance(tgenabled, (bytes, bytearray)) else tgenabled) == "O"
        assert proname == _FUNCTION
        # It must LOCK the campaign row, not read a racing snapshot.
        assert "FOR KEY SHARE" in prosrc
        assert "approved_at" in prosrc
        for status in ("publication_pending", "publishing", "published"):
            assert status in prosrc
        for status in ("rejected", "cancelled"):
            assert status in prosrc

        # ── behavior: publication refused before approval, allowed after ────
        book_id, toc_id, job_id = await _seed_lesson(engine)
        campaign_id, target_id = uuid.uuid4(), uuid.uuid4()
        # These are deliberately raw schema-level statements: they assert DDL
        # and TRIGGER behavior and do not depend on plan contents at all. They
        # still pass an OBJECT for `phase_plan` ('{}'::jsonb, not '[]'::jsonb)
        # because a bare array is no longer a legal plan shape — the stored
        # value is `RegenerationPhasePlan.to_json()`. They deliberately do NOT
        # import the planner: this file must stay a pure schema test.
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO regeneration_campaigns (id, status, selection_spec,"
                        " requested_phases, excluded_phases, launch_contract, created_at,"
                        " updated_at) VALUES (:id,'draft','{}'::jsonb,'[]'::jsonb,"
                        "'[]'::jsonb,'{}'::jsonb,now(),now())"
                    ),
                    {"id": campaign_id},
                )
                await conn.execute(
                    text(
                        "INSERT INTO regeneration_targets (id, campaign_id, toc_entry_id,"
                        " output_language, source_job_id, phase_plan, status, created_at,"
                        " updated_at) VALUES (:id,:c,:toc,'uz',:job,'{}'::jsonb,'planned',"
                        "now(),now())"
                    ),
                    {"id": target_id, "c": campaign_id, "toc": toc_id, "job": job_id},
                )

            # Direct SQL — no service layer in the way. The DB itself refuses.
            with pytest.raises(IntegrityError) as exc:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE regeneration_targets SET status='publication_pending',"
                            " publication_released_at=now() WHERE id=:id"
                        ),
                        {"id": target_id},
                    )
            assert "approved" in str(exc.value)

            # An INSERT straight into a publication state is refused too.
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO regeneration_targets (id, campaign_id, toc_entry_id,"
                            " output_language, source_job_id, phase_plan, status,"
                            " publication_released_at, created_at, updated_at) VALUES "
                            "(:id,:c,:toc,'ru',:job,'{}'::jsonb,'publication_pending',now(),"
                            "now(),now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "c": campaign_id,
                            "toc": toc_id,
                            "job": job_id,
                        },
                    )

            # After approval the very same statement succeeds.
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE regeneration_campaigns SET status='approved',"
                        " approved_at=now() WHERE id=:id"
                    ),
                    {"id": campaign_id},
                )
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE regeneration_targets SET status='publication_pending',"
                        " publication_released_at=now() WHERE id=:id"
                    ),
                    {"id": target_id},
                )
            async with engine.begin() as conn:
                got = await conn.scalar(
                    text("SELECT status FROM regeneration_targets WHERE id=:id"),
                    {"id": target_id},
                )
            assert got == "publication_pending"

            # A cancelled campaign closes the door again for a NEW transition.
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE regeneration_campaigns SET status='cancelled' WHERE id=:id"
                    ),
                    {"id": campaign_id},
                )
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE regeneration_targets SET status='publishing' WHERE id=:id"
                        ),
                        {"id": target_id},
                    )
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM regeneration_targets WHERE campaign_id=:c"),
                    {"c": campaign_id},
                )
                await conn.execute(
                    text("DELETE FROM regeneration_campaigns WHERE id=:c"),
                    {"c": campaign_id},
                )
            await _cleanup_lesson(engine, book_id, toc_id, job_id)

        # ── downgrade removes everything, trigger AND function ──────────────
        _run_alembic(["downgrade", _PREV])
        assert not await _has_table(engine, "regeneration_campaigns")
        assert not await _has_table(engine, "regeneration_targets")
        assert not await _has_column(engine, "homework_jobs", "revision_of_job_id")
        assert not await _has_column(engine, "homework_jobs", "regeneration_target_id")
        assert not await _has_column(engine, "homework_jobs", "session_limit_strategy")
        assert not await _has_column(engine, "phase_outputs", "copied_from_phase_output_id")
        assert await _constraint_def(engine, "ck_homework_jobs_revision_pair") is None
        for name in (
            "ck_homework_jobs_session_limit_strategy",
            "ck_homework_jobs_revision_session_limit_strategy",
        ):
            assert await _constraint_def(engine, name) is None, f"{name} survived"
        async with engine.begin() as conn:
            leftover = await conn.scalar(
                text("SELECT proname FROM pg_proc WHERE proname=:n"), {"n": _FUNCTION}
            )
        assert leftover is None, "downgrade left the trigger function behind"
    finally:
        _run_alembic(["upgrade", "head"])
        await engine.dispose()



async def test_0064_adds_campaign_version_and_reviewed_destination_and_reverts():
    """0064 is additive: five nullable destination columns on
    `regeneration_targets`, one nullable version column on
    `regeneration_campaigns`, and the two named CHECKs that police them.

    `publication_version` is a NAME COLLISION across the two tables — 0063
    already put one on `regeneration_targets` (the per-lesson allocation). Every
    assertion here is table-scoped, and the downgrade half explicitly proves the
    TARGET column survives while the CAMPAIGN one goes.
    """
    engine = create_async_engine(_DB_URL)
    try:
        # ── RED baseline: nothing of 0064 exists at 0063 ────────────────────
        _run_alembic(["upgrade", "head"])
        _run_alembic(["downgrade", _PREV_0064])
        assert not await _has_column(
            engine, "regeneration_campaigns", "publication_version"
        )
        for column in _DESTINATION_COLUMNS:
            assert not await _has_column(engine, "regeneration_targets", column), column
        assert await _constraint_def(engine, _CAMPAIGN_VERSION_CHECK) is None
        assert await _constraint_def(engine, _DESTINATION_CHECK) is None

        # ── upgrade ─────────────────────────────────────────────────────────
        _run_alembic(["upgrade", "head"])
        assert await _has_column(
            engine, "regeneration_campaigns", "publication_version"
        ), "regeneration_campaigns.publication_version missing"
        for column in _DESTINATION_COLUMNS:
            assert await _has_column(engine, "regeneration_targets", column), column

        # Additive means additive: a pre-0064 row shape must still insert, so
        # every new column has to be nullable.
        async with engine.begin() as conn:
            nullable = dict(
                (
                    await conn.execute(
                        text(
                            "SELECT column_name, is_nullable FROM information_schema.columns"
                            " WHERE table_name='regeneration_targets'"
                            " AND column_name = ANY(:cols)"
                        ),
                        {"cols": list(_DESTINATION_COLUMNS)},
                    )
                ).all()
            )
            campaign_nullable = await conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns"
                    " WHERE table_name='regeneration_campaigns'"
                    " AND column_name='publication_version'"
                )
            )
        assert campaign_nullable == "YES"
        assert nullable == {c: "YES" for c in _DESTINATION_COLUMNS}, nullable

        version_rule = await _constraint_def(engine, _CAMPAIGN_VERSION_CHECK)
        assert version_rule is not None, f"{_CAMPAIGN_VERSION_CHECK} missing"
        assert "publication_version" in version_rule
        assert ">= 2" in version_rule

        destination_rule = await _constraint_def(engine, _DESTINATION_CHECK)
        assert destination_rule is not None, f"{_DESTINATION_CHECK} missing"
        # Three-valued logic: a CHECK is SATISFIED by UNKNOWN, so a rule built
        # from bare `= 'reuse'` comparisons ACCEPTS the half-filled shapes it
        # was written to refuse. What is pinned here is that the comparisons
        # against the two policy columns are TOTAL — PostgreSQL stores
        # `a IS NOT DISTINCT FROM b` back as `NOT (a IS DISTINCT FROM b)`, so
        # the assertion is on the operator, not on the spelling.
        assert "IS DISTINCT FROM" in destination_rule, destination_rule
        # The one partial comparison left in the rule — `IN ('reuse','create')`,
        # stored as `= ANY (ARRAY[...])` — sits behind an explicit `IS NOT
        # NULL`, the same pairing 0063 uses for the revision session-limit
        # strategy. That pairing is redundant defence-in-depth rather than what
        # closes the UNKNOWN hole: the `IS NOT DISTINCT FROM` branches asserted
        # above already force the same policy set and already reject NULL. It is
        # pinned anyway because dropping the guard while keeping the `IN` would
        # put a partial comparison back into the predicate for no gain.
        assert "notion_parent_policy IS NOT NULL" in destination_rule, destination_rule
        for policy in ("notion_parent_policy", "notion_container_policy"):
            for literal in ("'reuse'::text", "'create'::text"):
                assert f"({policy})::text = {literal}" not in destination_rule, (
                    f"{policy} is compared with an UNGUARDED `=` against "
                    f"{literal} — that evaluates to NULL when the policy is "
                    f"missing, and a NULL CHECK result PASSES"
                )
        for column in _DESTINATION_COLUMNS:
            assert column in destination_rule, column

        # ── downgrade removes 0064 and NOTHING of 0063 ──────────────────────
        _run_alembic(["downgrade", _PREV_0064])
        assert not await _has_column(
            engine, "regeneration_campaigns", "publication_version"
        )
        for column in _DESTINATION_COLUMNS:
            assert not await _has_column(engine, "regeneration_targets", column), column
        assert await _constraint_def(engine, _CAMPAIGN_VERSION_CHECK) is None
        assert await _constraint_def(engine, _DESTINATION_CHECK) is None
        # The same-named 0063 column on the OTHER table is untouched, and so are
        # the tables themselves.
        assert await _has_column(engine, "regeneration_targets", "publication_version")
        assert await _has_table(engine, "regeneration_campaigns")
        assert await _has_table(engine, "regeneration_targets")
        assert (
            await _constraint_def(engine, "ck_regeneration_targets_published_complete")
            is not None
        )
    finally:
        _run_alembic(["upgrade", "head"])
        await engine.dispose()
