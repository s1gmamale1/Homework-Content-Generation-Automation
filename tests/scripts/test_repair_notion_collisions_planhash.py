"""Pure (no DB, no Notion, no network) tests for the plan-hash + manifest
helpers in `scripts/repair_notion_collisions.py`.

These guard the operator-safety contract added on top of the collision
repair: `--apply` must only be allowed to run against the EXACT plan an
operator reviewed (the hash), and a persisted manifest must capture enough
of that plan's expected state that a later `--refresh-notion` step can act
on it AFTER `--apply` has already changed the DB (at which point the
collision query returns nothing, so the plan can no longer be re-derived).

All fixtures are built in-memory from frozen dataclasses with fixed
timestamps — no `datetime.now()`, no DB session, no Notion client.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from scripts.repair_notion_collisions import (
    DRY_RUN_FOOTER,
    GroupPlan,
    JobRow,
    SectionRow,
    dry_run_footer_lines,
    manifest_from_plans,
    manifest_load,
    plan_hash,
)

T0 = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


def _h(n: int) -> datetime:
    return T0 + timedelta(hours=n)


PAGE_A = "aaaaaaaa-1111-2222-3333-444444444444"
PAGE_B = "bbbbbbbb-1111-2222-3333-444444444444"

OWNER_SECTION_ID = UUID("00000000-0000-0000-0000-000000000001")
NON_OWNER_SECTION_ID = UUID("00000000-0000-0000-0000-000000000002")
OWNER_JOB_ID = UUID("00000000-0000-0000-0000-000000000011")
NON_OWNER_JOB_ID = UUID("00000000-0000-0000-0000-000000000012")

OTHER_OWNER_SECTION_ID = UUID("00000000-0000-0000-0000-000000000021")
OTHER_NON_OWNER_SECTION_ID = UUID("00000000-0000-0000-0000-000000000022")
OTHER_OWNER_JOB_ID = UUID("00000000-0000-0000-0000-000000000031")
OTHER_NON_OWNER_JOB_ID = UUID("00000000-0000-0000-0000-000000000032")


def _owner_job(archived_at: datetime | None = None) -> JobRow:
    return JobRow(
        job_id=OWNER_JOB_ID,
        notion_archived_at=archived_at if archived_at is not None else _h(0),
        completed_at=_h(0),
        status="done",
        output_language="uz",
    )


def _non_owner_job(archived_at: datetime | None) -> JobRow:
    return JobRow(
        job_id=NON_OWNER_JOB_ID,
        notion_archived_at=archived_at,
        completed_at=_h(1),
        status="done",
        output_language="uz",
    )


def _owner_section() -> SectionRow:
    job = _owner_job()
    return SectionRow(
        section_id=OWNER_SECTION_ID,
        page_id=PAGE_A,
        section_title="Owner Lesson",
        page_start=1,
        subject="math",
        grade="5",
        stamped_job_id=OWNER_JOB_ID,
        jobs=(job,),
    )


def _non_owner_section(archived_at: datetime | None = _h(2)) -> SectionRow:
    job = _non_owner_job(archived_at)
    return SectionRow(
        section_id=NON_OWNER_SECTION_ID,
        page_id=PAGE_A,
        section_title="Non-owner Lesson",
        page_start=5,
        subject="math",
        grade="5",
        stamped_job_id=NON_OWNER_JOB_ID,
        jobs=(job,),
    )


def _make_plan_a(non_owner_archived_at: datetime | None = _h(2)) -> GroupPlan:
    owner = _owner_section()
    non_owner = _non_owner_section(non_owner_archived_at)
    return GroupPlan(
        page_id=PAGE_A,
        sections=(owner, non_owner),
        owner=owner,
        owner_source="stamped_push",
        owner_push=_h(0),
        ordering_disagreement=False,
    )


def _make_plan_b() -> GroupPlan:
    owner_job = JobRow(
        job_id=OTHER_OWNER_JOB_ID,
        notion_archived_at=_h(10),
        completed_at=_h(10),
        status="done",
        output_language="ru",
    )
    non_owner_job = JobRow(
        job_id=OTHER_NON_OWNER_JOB_ID,
        notion_archived_at=_h(12),
        completed_at=_h(12),
        status="done",
        output_language="ru",
    )
    owner = SectionRow(
        section_id=OTHER_OWNER_SECTION_ID,
        page_id=PAGE_B,
        section_title="Other Owner",
        page_start=1,
        subject="biology",
        grade="7",
        stamped_job_id=OTHER_OWNER_JOB_ID,
        jobs=(owner_job,),
    )
    non_owner = SectionRow(
        section_id=OTHER_NON_OWNER_SECTION_ID,
        page_id=PAGE_B,
        section_title="Other Non-owner",
        page_start=9,
        subject="biology",
        grade="7",
        stamped_job_id=OTHER_NON_OWNER_JOB_ID,
        jobs=(non_owner_job,),
    )
    return GroupPlan(
        page_id=PAGE_B,
        sections=(owner, non_owner),
        owner=owner,
        owner_source="stamped_push",
        owner_push=_h(10),
        ordering_disagreement=False,
    )


def test_plan_hash_is_deterministic_and_order_independent():
    plan_a = _make_plan_a()
    plan_b = _make_plan_b()

    hash_forward = plan_hash([plan_a, plan_b])
    hash_backward = plan_hash([plan_b, plan_a])
    hash_forward_again = plan_hash([plan_a, plan_b])

    assert hash_forward == hash_backward
    assert hash_forward == hash_forward_again


def test_plan_hash_covers_expected_state():
    """Two plans identical in every id but differing in a job's expected
    notion_archived_at must hash DIFFERENTLY — an ids-only hash would
    wrongly treat these as the same plan."""
    plan_variant_1 = _make_plan_a(non_owner_archived_at=_h(2))
    plan_variant_2 = _make_plan_a(non_owner_archived_at=_h(3))

    hash_1 = plan_hash([plan_variant_1])
    hash_2 = plan_hash([plan_variant_2])

    assert hash_1 != hash_2


def test_plan_hash_covers_expected_state_page_id_change():
    """Same ids, but a non-owner's page_id (via a different group page_id)
    differs — must also produce a different hash."""
    plan_a = _make_plan_a()
    # Same sections/owner but a different page_id -> different expected
    # notion_homework_page_id for the non-owner.
    owner = _owner_section()
    non_owner = _non_owner_section()
    plan_a_diff_page = GroupPlan(
        page_id=PAGE_B,
        sections=(owner, non_owner),
        owner=owner,
        owner_source="stamped_push",
        owner_push=_h(0),
        ordering_disagreement=False,
    )

    assert plan_hash([plan_a]) != plan_hash([plan_a_diff_page])


def test_manifest_roundtrips(tmp_path):
    plans = [_make_plan_a(), _make_plan_b()]
    manifest = manifest_from_plans(plans)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = manifest_load(str(manifest_path))

    assert loaded["hash"] == manifest["hash"]
    assert loaded["hash"] == plan_hash(plans)
    assert loaded["owners"] == manifest["owners"]
    owner_page_ids = {o["page_id"] for o in loaded["owners"]}
    assert owner_page_ids == {PAGE_A, PAGE_B}


def test_manifest_preserves_row_push_winning_job():
    """A row-level push can predate the stamped job; refresh must use it."""
    from scripts.repair_notion_collisions import (
        GroupPlan, JobRow, SectionRow, manifest_from_plans,
    )
    early = uuid4()
    stamped = uuid4()
    section = SectionRow(
        section_id=uuid4(), page_id="page", section_title="x", page_start=None,
        subject="math", grade="5", stamped_job_id=stamped,
        jobs=(
            JobRow(early, datetime(2026, 1, 1), datetime(2026, 1, 3), "done", "uz"),
            JobRow(stamped, datetime(2026, 1, 2), datetime(2026, 1, 2), "done", "uz"),
        ),
    )
    plan = GroupPlan("page", (section,), section, "row_push", datetime(2026, 1, 1), False)
    assert manifest_from_plans([plan])["owners"][0]["job_id"] == str(early)


def test_dry_run_footer_includes_plan_hash():
    plans = [_make_plan_a()]
    lines = dry_run_footer_lines(plans)

    assert lines[0] == DRY_RUN_FOOTER
    assert lines[1] == f"plan-hash={plan_hash(plans)}"


def test_manifest_load_rejects_tampered_hash(tmp_path):
    plans = [_make_plan_a()]
    manifest = manifest_from_plans(plans)

    # Tamper with the expected state after computing the hash, simulating a
    # hand-edited or corrupted manifest file.
    manifest["expected"]["sections"][0]["notion_archived_job_id"] = "tampered"

    manifest_path = tmp_path / "tampered.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError):
        manifest_load(str(manifest_path))
