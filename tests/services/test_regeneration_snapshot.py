"""`regeneration_snapshot.create_revision_job` — the immutable snapshot copy.

A revision job is created ONCE per target and must be a complete, runnable
homework job from the instant it exists: the phases the campaign did not
regenerate are already sitting in `phase_outputs` as `done` rows, so the
ordinary pipeline resumes into it and only re-runs what the plan asked for.

Everything here is about *provenance* and *not paying twice*:

* book/section/subject/language come from the IMMEDIATE SOURCE job — never from
  a campaign-wide setting, because one campaign may hold a UZ and an RU target
  for the same lesson;
* every provider/model/transport and the session-limit strategy come from the
  STORED, already-resolved contract — never from a second read of the mutable
  `launch_defaults` row or of the fleet-wide default, which is what would make
  a canary wave and its bulk wave run different policies;
* copied phases are copied column-for-column with a `copied_from_phase_output_id`
  provenance link and NO cloned `agent_usages` row — the only usage row a copy
  may produce is the existing zero-cost `<cache>` extract marker.

The real-DB tests are the load-bearing ones: the constraints
(`ck_homework_jobs_revision_*`, `uq_phase_output_job_order`,
`uq_homework_jobs_regeneration_target_id`) are half of the contract.
"""
from __future__ import annotations

import os
import uuid

import pytest
from pydantic import ValidationError

from app.schemas.regeneration_contract import ResolvedLaunchContract
from app.services import regeneration_snapshot
from app.services.flows import flow_for
from app.services.regeneration_planner import build_phase_plan

_SUBJECT = "math-algebra"
_CANONICAL = ("extract", *flow_for(_SUBJECT))
_PLAN = build_phase_plan(subject=_SUBJECT, selected_phases=["flashcards"])
_PHASE_PLAN = _PLAN.to_json()

_CONTRACT = ResolvedLaunchContract(
    provider="gemini", model="gemini-3.5-flash", transport="api",
    extract_provider="gemini", extract_model="gemini-3.5-flash-lite",
    judge_provider="claude", judge_model="claude-opus-4-7",
    solver_provider="claude", solver_model="claude-opus-4-7",
    session_limit_strategy="switch",
)

db_only = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)


# ─────────────────────────────────────────────────────────────────────────
# no DB: the contract boundary refuses, it never repairs
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "override, why",
    [
        ({"session_limit_strategy": "inherit"}, "session_limit_strategy"),
        ({"judge_provider": None}, "judge_provider"),
        ({"judge_model": None}, "judge_model"),
        ({"model": None}, "content model"),
    ],
)
def test_stored_contract_that_is_not_resolved_is_refused(override, why):
    """`ensure_resolved` is the ONE boundary and it verifies, never resolves.

    A revision has `batch_id=NULL`, so a contract still carrying `'inherit'`
    would fall through to the mutable fleet-wide default at run time — the exact
    second resolution the whole design exists to prevent.
    """
    stored = {**_CONTRACT.model_dump(), **override}
    with pytest.raises(ValidationError):
        regeneration_snapshot.ensure_resolved(stored)


def test_snapshot_uses_the_shared_contract_boundary_not_a_second_one():
    """One definition of "resolved" for the whole feature."""
    from app.schemas import regeneration_contract

    assert regeneration_snapshot.ensure_resolved is regeneration_contract.ensure_resolved
    assert regeneration_snapshot.ensure_resolved(_CONTRACT.model_dump()) == _CONTRACT


# ─────────────────────────────────────────────────────────────────────────
# real Postgres
# ─────────────────────────────────────────────────────────────────────────


async def _seed(
    *,
    language: str = "uz",
    complete: bool = True,
    contract: ResolvedLaunchContract | None = None,
    with_source: bool = True,
):
    """A book + TOC entry + a done source job carrying a FULL phase snapshot,
    plus a draft campaign and one planned target pointing at it."""
    from app.db import SessionLocal
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        book = Book(
            subject=_SUBJECT, original_filename="regen_snapshot.pdf",
            content_sha256=uuid.uuid4().hex * 2, file_size_bytes=1,
            status="toc_ready",
        )
        session.add(book)
        await session.flush()
        toc = TOCEntry(book_id=book.id, section_title="L1", order_index=0)
        session.add(toc)
        await session.flush()
        source = HomeworkJob(
            book_id=book.id, toc_entry_id=toc.id, subject=_SUBJECT,
            status="done", provider="claude", model="claude-sonnet-4-6",
            transport="cli", output_language=language,
        )
        session.add(source)
        await session.flush()
        phases = _CANONICAL if complete else _CANONICAL[:-1]
        rows = {}
        for order, name in enumerate(phases):
            po = PhaseOutput(
                job_id=source.id, phase_name=name, phase_order=order,
                prompt_hash=f"builtin:{name}:v9", model_name="claude-sonnet-4-6",
                provider="claude", output_md=f"# {name}\nbody",
                tokens_input=11 + order, tokens_output=22 + order,
                status="done", error_message=None,
                validation_warnings=[f"lint:{name}"],
                judge_status="ok", solver_status="ok",
                content_json={"phase": name} if name == "flashcards" else None,
                authoring_mode="structured" if name == "flashcards" else "markdown_builtin",
                content_schema_version="1.4" if name == "flashcards" else None,
                renderer_version="r7" if name == "flashcards" else None,
                claim_token=uuid.uuid4(),
            )
            session.add(po)
            await session.flush()
            rows[name] = po.id
        campaign = RegenerationCampaign(
            status="draft", selection_spec={}, requested_phases=["flashcards"],
            excluded_phases=[],
            launch_contract=(contract or _CONTRACT).model_dump(),
        )
        session.add(campaign)
        await session.flush()
        target = RegenerationTarget(
            campaign_id=campaign.id, toc_entry_id=toc.id,
            output_language=language, phase_plan=_PHASE_PLAN,
            source_job_id=source.id if with_source else None,
            status="planned",
        )
        session.add(target)
        await session.commit()
        return {
            "book_id": book.id, "toc_id": toc.id, "source_id": source.id,
            "campaign_id": campaign.id, "target_id": target.id,
            "source_phase_ids": rows,
        }


async def _purge(ids: dict) -> None:
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput
    from app.models.regeneration_campaign import RegenerationCampaign
    from app.models.regeneration_target import RegenerationTarget
    from app.models.toc_entry import TOCEntry

    async with SessionLocal() as session:
        from sqlalchemy import select

        job_ids = list((await session.execute(
            select(HomeworkJob.id).where(
                HomeworkJob.book_id == ids["book_id"]))).scalars().all())
        await session.execute(
            delete(AgentUsage).where(
                AgentUsage.homework_job_id.in_(job_ids or [uuid.uuid4()])))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.revision_of_job_id.is_not(None))
            .where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(RegenerationTarget).where(
                RegenerationTarget.campaign_id == ids["campaign_id"]))
        await session.execute(
            delete(RegenerationCampaign).where(
                RegenerationCampaign.id == ids["campaign_id"]))
        await session.execute(
            delete(PhaseOutput).where(PhaseOutput.job_id == ids["source_id"]))
        await session.execute(
            delete(HomeworkJob).where(HomeworkJob.book_id == ids["book_id"]))
        await session.execute(
            delete(TOCEntry).where(TOCEntry.book_id == ids["book_id"]))
        await session.execute(delete(Book).where(Book.id == ids["book_id"]))
        await session.commit()


@pytest.fixture()
async def seeded():
    ids = await _seed()
    try:
        yield ids
    finally:
        await _purge(ids)


async def _create(ids, **kw):
    from app.db import SessionLocal

    async with SessionLocal() as session:
        return await regeneration_snapshot.create_revision_job(
            session, target_id=ids["target_id"], launch_contract=_CONTRACT, **kw)


@db_only
async def test_revision_copies_source_book_section_subject_and_language(seeded):
    """Provenance is the IMMEDIATE SOURCE's, never a campaign-wide value."""
    job = await _create(seeded)
    assert job.book_id == seeded["book_id"]
    assert job.toc_entry_id == seeded["toc_id"]
    assert job.subject == _SUBJECT
    assert job.output_language == "uz"
    assert job.revision_of_job_id == seeded["source_id"]
    assert job.regeneration_target_id == seeded["target_id"]
    assert job.kind == "homework"
    assert job.batch_id is None
    assert job.selected_phases is None
    assert job.status == "pending"


@db_only
async def test_revision_language_follows_a_ru_source_not_the_campaign():
    """The RU limb of the same rule, proven separately: a UZ default anywhere
    in the campaign must not reach an RU lesson's revision."""
    ids = await _seed(language="ru")
    try:
        job = await _create(ids)
        assert job.output_language == "ru"
    finally:
        await _purge(ids)


@db_only
async def test_revision_applies_the_contract_content_selection(seeded):
    job = await _create(seeded)
    assert (job.provider, job.model, job.transport) == (
        "gemini", "gemini-3.5-flash", "api")
    # NOT the source job's claude/cli pick — the contract is the authority.
    assert job.provider != "claude"


@db_only
async def test_revision_job_stamps_every_role_provider_and_model_explicitly(seeded):
    """No NULL role column. A revision has `batch_id=NULL`, so a NULL role pair
    would be resolved from the MUTABLE `launch_defaults` row at whatever moment
    the job happened to run — it would be the only job in the system like that.
    """
    job = await _create(seeded)
    for role in ("extract", "judge", "solver"):
        provider = getattr(job, f"{role}_provider")
        model = getattr(job, f"{role}_model")
        assert provider == getattr(_CONTRACT, f"{role}_provider"), role
        assert model == getattr(_CONTRACT, f"{role}_model"), role
        assert provider is not None and model is not None, role
        assert getattr(job, f"{role}_transport") == getattr(
            _CONTRACT, f"{role}_transport"), role


@db_only
async def test_revision_job_copies_the_contracts_concrete_session_limit_strategy(
    seeded, monkeypatch
):
    """Copied VERBATIM — never re-resolved.

    `launch_canary` and `approve_canary` call this at two wall-clock moments
    separated by a human gate. A second read of the fleet-wide default would
    give one immutable campaign two meanings, so the fleet-wide default is
    monkeypatched to the OTHER value while the copy is made.
    """
    from app.config import settings
    from app.services import agent_models

    assert _CONTRACT.session_limit_strategy == "switch"
    monkeypatch.setattr(settings, "session_limit_strategy", "pause")

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError(
            "create_revision_job called resolve_session_limit_strategy — the "
            "campaign's approved value must be COPIED, not re-resolved")

    monkeypatch.setattr(agent_models, "resolve_session_limit_strategy", _boom)
    monkeypatch.setattr(
        regeneration_snapshot, "resolve_session_limit_strategy", _boom, raising=False)

    job = await _create(seeded)
    assert job.session_limit_strategy == "switch"


@db_only
async def test_revision_refuses_a_stored_contract_that_is_still_inherit(seeded):
    from app.db import SessionLocal

    stored = {**_CONTRACT.model_dump(), "session_limit_strategy": "inherit"}
    async with SessionLocal() as session:
        with pytest.raises(ValidationError):
            await regeneration_snapshot.create_revision_job(
                session, target_id=seeded["target_id"], launch_contract=stored)


@db_only
async def test_create_revision_job_refuses_a_target_whose_source_job_id_is_null():
    """A child-first purge nulls the link while the reporting row survives: the
    target still exists but there is no snapshot to copy from."""
    ids = await _seed(with_source=False)
    try:
        with pytest.raises(regeneration_snapshot.MissingRevisionSource):
            await _create(ids)
    finally:
        await _purge(ids)


@db_only
async def test_revision_refuses_an_incomplete_source_snapshot():
    """`validate_complete_snapshot` is the ONLY completeness predicate."""
    ids = await _seed(complete=False)
    try:
        with pytest.raises(regeneration_snapshot.IncompleteSnapshot) as exc:
            await _create(ids)
        assert "reflection" in str(exc.value)
    finally:
        await _purge(ids)


@db_only
async def test_revision_refuses_a_source_row_at_a_drifted_phase_order(seeded):
    """A verified canonical order or nothing — never a silent renumber."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput

    async with SessionLocal() as session:
        await session.execute(
            update(PhaseOutput)
            .where(PhaseOutput.id == seeded["source_phase_ids"]["reflection"])
            .values(phase_order=99))
        await session.commit()
    with pytest.raises(regeneration_snapshot.IncompleteSnapshot) as exc:
        await _create(seeded)
    assert "order drifted" in str(exc.value)


@db_only
async def test_copied_phase_rows_carry_every_column_and_the_provenance_link(seeded):
    """The exact copied-column set, verified field by field against the source."""
    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput
    from sqlalchemy import select

    job = await _create(seeded)
    async with SessionLocal() as session:
        copies = {
            r.phase_name: r for r in (await session.execute(
                select(PhaseOutput).where(PhaseOutput.job_id == job.id))).scalars()
        }
        sources = {
            r.phase_name: r for r in (await session.execute(
                select(PhaseOutput).where(
                    PhaseOutput.job_id == seeded["source_id"]))).scalars()
        }

    assert set(copies) == set(_PLAN.copied_phases)
    assert "flashcards" not in copies, "a REGENERATED phase must be left absent"
    assert "extract" in copies

    columns = (
        "phase_name", "phase_order", "prompt_hash", "model_name", "provider",
        "output_md", "tokens_input", "tokens_output", "status", "error_message",
        "validation_warnings", "judge_status", "solver_status", "started_at",
        "completed_at", "content_json", "authoring_mode",
        "content_schema_version", "renderer_version",
    )
    for name, copy in copies.items():
        src = sources[name]
        for column in columns:
            assert getattr(copy, column) == getattr(src, column), f"{name}.{column}"
        assert copy.id != src.id
        assert copy.job_id == job.id
        assert copy.claim_token is None, (
            f"{name}: the SOURCE run's fencing token must never be copied")
        assert copy.copied_from_phase_output_id == src.id


@db_only
async def test_copied_rows_sit_at_the_verified_canonical_phase_order(seeded):
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput

    job = await _create(seeded)
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(PhaseOutput).where(PhaseOutput.job_id == job.id))).scalars())
    canonical = {name: i for i, name in enumerate(_CANONICAL)}
    for row in rows:
        assert row.phase_order == canonical[row.phase_name], row.phase_name


@db_only
async def test_copied_rows_satisfy_the_pipelines_done_predicate(seeded):
    """The exact `pipeline._done_phase_md` predicate — the pipeline must SKIP
    every copied phase, including the structured one whose `output_md` is
    empty."""
    from sqlalchemy import select, update

    from app.db import SessionLocal
    from app.models.phase_output import PhaseOutput
    from app.services.pipeline import _done_phase_md

    # Make the structured copy the hard case: content_json only, output_md "".
    async with SessionLocal() as session:
        await session.execute(
            update(PhaseOutput)
            .where(PhaseOutput.id == seeded["source_phase_ids"]["reflection"])
            .values(output_md="", content_json={"blocks": []}))
        await session.commit()
    job = await _create(seeded)
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(PhaseOutput).where(PhaseOutput.job_id == job.id))).scalars())
    done = _done_phase_md(rows)
    assert set(done) == set(_PLAN.copied_phases)


@db_only
async def test_copying_the_extract_records_the_free_cache_marker_and_nothing_paid(
    seeded,
):
    """Zero cloned paid usage. The only row a copy may write is the existing
    zero-cost `<cache>` lesson.extract marker, carrying source provenance."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.agent_usage import AgentUsage

    assert "extract" in _PLAN.copied_phases
    job = await _create(seeded)
    async with SessionLocal() as session:
        rows = list((await session.execute(
            select(AgentUsage).where(
                AgentUsage.homework_job_id == job.id))).scalars())
    assert len(rows) == 1, f"expected exactly the cache marker, got {rows}"
    marker = rows[0]
    assert marker.operation == "lesson.extract"
    assert (marker.provider, marker.model_name) == ("<cache>", "<cache>")
    assert marker.prompt_tokens == marker.output_tokens == marker.total_tokens == 0
    assert marker.raw_envelope["cache_hit"] is True
    assert marker.raw_envelope["source_job_id"] == str(seeded["source_id"])
    assert marker.raw_envelope["source_phase_output_id"] == str(
        seeded["source_phase_ids"]["extract"])


@db_only
async def test_create_revision_job_is_idempotent_and_never_re_staggers(seeded):
    """Repeat calls return the SAME job with its ORIGINAL schedule — a retried
    wave must not push an already-queued revision further into the future."""
    first = await _create(seeded, start_offset_seconds=0)
    scheduled_at = first.scheduled_at
    second = await _create(seeded, start_offset_seconds=3600)
    assert second.id == first.id
    assert second.scheduled_at == scheduled_at

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.phase_output import PhaseOutput

    async with SessionLocal() as session:
        n_jobs = (await session.execute(
            select(func.count()).select_from(HomeworkJob)
            .where(HomeworkJob.regeneration_target_id == seeded["target_id"]))
        ).scalar_one()
        n_phases = (await session.execute(
            select(func.count()).select_from(PhaseOutput)
            .where(PhaseOutput.job_id == first.id))).scalar_one()
    assert n_jobs == 1
    assert n_phases == len(_PLAN.copied_phases), "phases must not be copied twice"


@db_only
async def test_start_offset_seconds_reaches_scheduled_at_at_first_creation(seeded):
    """Stagger is applied ATOMICALLY at insert (DB clock), not by a later
    UPDATE — a worker must never be able to claim the row in between."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    job = await _create(seeded, start_offset_seconds=1800)
    async with SessionLocal() as session:
        row = (await session.execute(
            select(HomeworkJob).where(HomeworkJob.id == job.id))).scalar_one()
        now = (await session.execute(select(__import__(
            "sqlalchemy").func.now()))).scalar_one()
    assert (row.scheduled_at - now).total_seconds() > 1500
