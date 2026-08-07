# Fenced Job Leases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every job claim a per-execution UUID token, folded into the existing `claimed_at`/`claimed_by` lease, so a reclaimed job can never be mutated, completed, or archived by an obsolete worker execution.

**Architecture:** Add a `claim_token` UUID to `homework_jobs` and `phase_outputs`, minted on claim and rotated/cleared on every reclaim/requeue. Every worker-owned write gains `AND claim_token = :token` in its WHERE clause; a 0-row match is disambiguated into `LeaseLost` vs `CancelRequested` and propagated so the obsolete worker cancels its own execution and mutates nothing. An append-only `job_lease_events` ledger records every claim/reclaim/release for audit. This is a **single** lease primitive (the token lives inside the existing timestamp lease), not a parallel one.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, Alembic, PostgreSQL (`FOR UPDATE` row locks, `ON CONFLICT` for the ledger), pytest + pytest-asyncio, real-Postgres integration tests (`RUN_DB_INTEGRATION=1`).

## Global Constraints

- **Two gates.** This plan is **Gate 1 only**: code, migration `0052`, deterministic real-Postgres race tests, whole-branch review. Gate 2 (deployment + paid 4→40 soak) is a **separate** approval documented at the end — **no paid load test is authorized by merging this plan.**
- **Branch base:** cut the feature branch **after PR #118 (`fix/content-json-gate-corrections`) merges to `Nggaev-v2`** — it rewrites `pipeline.py` where fencing writes + lease-loss propagation land, so cutting earlier guarantees a painful `pipeline.py` rebase. (#119 `feat/structured-output-gate` is already **MERGED**; #118 is still **OPEN** as of 2026-08-06 — this branch-cut gate is currently UNMET.) Base the branch on `origin/Nggaev-v2` and re-verify every file:function anchor below (line numbers WILL have shifted; **locate by function name, not line number**).
- **Migration id:** `0052_job_lease_fencing`, `down_revision = "0051_launch_defaults_3x"` (current head; verified `0052` is unused across all branches). Columns nullable for rollback compatibility.
- **Fold, don't parallel:** the token is added to the EXISTING lease. `reclaim_stuck_jobs`, `fail_exhausted_pending_jobs`, and every `requeue_*`/`reset_for_retry` that already clears `claimed_at`/`claimed_by` MUST also clear `claim_token` in the same statement. Do **not** introduce a second lease table or a second staleness clock.
- **Compose with shipped guards — never replace them:**
  - 0155 cancel-wins: `_finalize_if_cancelling`'s "0 rows matched → re-read → finalize to `cancelled`" MUST still win. A fenced 0-row match distinguishes lease-lost from user-cancel by re-reading `status` + `claim_token`.
  - 0156 phase reconcile: reuse `phase_repo.reset_abandoned_phases` (marker-aware, evidence-preserving); do not hand-roll a second reset.
  - 0129 Notion: the token fence LAYERS ON TOP OF the `created_at > prior.created_at` direction guard and the `notion_archived_at` idempotency. Dropping `created_at` reintroduces the older-clobbers-newer regression.
  - Version gate (0133): reuse the existing worker identity `hostname:pid@sha` (`worker._worker_id`) and the `workers` registry staleness; the token is **additive** per-claim, not a new identity.
- **Keep `create_or_reset`** for phase init (it already fixes the retry-crash by resetting an existing row instead of a naive INSERT). It does **not** reuse completed phases — completed-phase reuse lives in the pipeline (`_done_phase_md`, consumed at the resume/pre-inject sites), which excludes done+nonempty phases from the pending set so `create_or_reset` is never reached for them. Add only: lease-verify + job-row `FOR UPDATE` before the phase write, phase-token stamping, and scoping the startup reconcile. Do **not** rewrite it into a raw `ON CONFLICT` upsert.
- **DB error ≠ ownership loss.** A connection/transport error during a fenced write or heartbeat must NOT be interpreted as `LeaseLost`; the next heartbeat retries.
- **Transitional signature default (keeps intermediate commits green):** every fenced worker-write function gains `claim_token: uuid.UUID | None = None`. `None` means "legacy unfenced" and preserves today's behavior, so a task can add the parameter to `jobs.py`/`phase_outputs.py` before its call sites in `pipeline.py`/`worker.py`/`main.py` are threaded, and the suite stays green at each commit boundary. **Task 11's review step greps every worker-owned `update(HomeworkJob)`/`update(PhaseOutput)` and asserts each real worker path now passes a non-None token** (only the documented admin/operator paths may omit it).
- **Timing constants already match** the design and must not be changed: `heartbeat_seconds=30`, `reclaim_stale_seconds=120`, `worker_registry_stale_seconds=90`, `job_timeout_seconds=1800` (all in `app/config.py`).
- **Test isolation:** real-DB tests pin `127.0.0.1` (not `localhost`) and run under `RUN_DB_INTEGRATION=1` with a scratch DB; the canonical bar is the suite WITHOUT the flag staying green.
- **Staging discipline:** stage only the files each task lists — never `git add -A` (other sessions commit to this branch's neighbors). The untracked root `Wishlist.md` and `scripts/export_homeworks.py` are never staged.

---

## File Structure

**New files**
- `alembic/versions/0052_job_lease_fencing.py` — token columns + `job_lease_events` table.
- `app/models/job_lease_event.py` — `JobLeaseEvent` ORM model.
- `app/services/lease.py` — immutable lease value types + outcome sentinels (`JobLease`, `ClaimedJob`, `LeaseLost`, `CancelRequested`, `HeartbeatOutcome`), plus `EVENT_*` constants.
- `app/repositories/lease_events.py` — `append_event(...)` (idempotent on the unique key).
- `tests/repositories/test_migration_0052_lease.py` — real-DB up/down + constraint tests.
- `tests/services/test_lease_types.py` — value-type/immutability tests.
- `tests/repositories/test_lease_fencing.py` — real-DB fencing race tests (job-level).
- `tests/repositories/test_phase_lease_fencing.py` — real-DB phase-write fencing.
- `tests/integration/test_reclaim_fencing_e2e.py` — the design's end-to-end race scenarios.
- `tests/api/test_startup_reconcile_scoped.py` — startup leaves peer-owned rows untouched.

**Modified files** (locate by function name — anchors will shift post-#118)
- `app/models/homework_job.py` — `claim_token` column.
- `app/models/phase_output.py` — `claim_token` column.
- `app/repositories/jobs.py` — `claim_next_job`, `touch_claim`, `set_status`, `mark_failed_with_retry`, `requeue_session_limited`, `requeue_slot_saturated`, `mark_cancelled`/`finalize_cancelled`, `_finalize_if_cancelling`, `heartbeat_check`, `reclaim_stuck_jobs`, `fail_exhausted_pending_jobs`, `reclaim_orphans_on_startup`, `reclaim_stale_cancelling`, `reset_for_retry`.
- `app/repositories/phase_outputs.py` — `create_or_reset`, `set_status`, `reset_abandoned_phases` (clear phase token).
- `app/services/worker.py` — `_execute_job`, `_heartbeat`, cancel/finalize paths; thread the token.
- `app/services/pipeline.py` — pass the lease into phase writes; let `LeaseLost`/`CancelRequested` propagate.
- `app/services/notion_archive.py` — `archive_job` token fence layered on the 0129 guard.
- `main.py` — `lifespan` startup reconcile scoped to reclaimed IDs.

---

## Task 1: Migration 0052 — token columns + audit ledger

**Files:**
- Create: `alembic/versions/0052_job_lease_fencing.py`
- Create: `app/models/job_lease_event.py`
- Modify: `app/models/homework_job.py` (add `claim_token`)
- Modify: `app/models/phase_output.py` (add `claim_token`)
- Test: `tests/repositories/test_migration_0052_lease.py`

**Interfaces:**
- Produces: `homework_jobs.claim_token: UUID|None`, `phase_outputs.claim_token: UUID|None`, table `job_lease_events(id, job_id, claim_token, event_type, owner, actor, reason, created_at)` with `UNIQUE(job_id, claim_token, event_type)`; ORM `JobLeaseEvent`.

- [ ] **Step 1: Write the failing real-DB migration test**

```python
# tests/repositories/test_migration_0052_lease.py
import os, uuid, pytest
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only")

from sqlalchemy import text

@pytest.mark.asyncio
async def test_0052_adds_token_columns_and_ledger(db_engine):
    async with db_engine.connect() as conn:
        cols_j = await conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='homework_jobs' AND column_name='claim_token'"))
        assert cols_j.first()[0] == "uuid"
        cols_p = await conn.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='phase_outputs' AND column_name='claim_token'"))
        assert cols_p.first()[0] == "uuid"
        # ledger uniqueness bites (asyncpg paramstyle via sa.text bound params)
        jid, tok = uuid.uuid4(), uuid.uuid4()
        ins = text("INSERT INTO job_lease_events (id, job_id, claim_token, event_type, owner) "
                   "VALUES (gen_random_uuid(), :jid, :tok, 'claimed', 'h:1@sha')")
        await conn.execute(ins, {"jid": jid, "tok": tok})
        with pytest.raises(Exception):
            await conn.execute(ins, {"jid": jid, "tok": tok})  # dup (job,token,event)
        await conn.rollback()
```

- [ ] **Step 2: Run it to verify it fails** — `RUN_DB_INTEGRATION=1 DATABASE_URL=... uv run pytest tests/repositories/test_migration_0052_lease.py -v` → FAIL (column/table missing).

- [ ] **Step 3: Write the migration**

```python
# alembic/versions/0052_job_lease_fencing.py
"""job lease fencing: claim_token columns + job_lease_events ledger"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB  # JSONB unused here; keep UUID

revision = "0052_job_lease_fencing"
down_revision = "0051_launch_defaults_3x"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("homework_jobs", sa.Column("claim_token", UUID(as_uuid=True), nullable=True))
    op.add_column("phase_outputs", sa.Column("claim_token", UUID(as_uuid=True), nullable=True))
    op.create_table(
        "job_lease_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),  # NO FK: ledger survives job deletion
        sa.Column("claim_token", UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("owner", sa.String(128), nullable=True),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # NB: every event this system writes carries a NON-NULL token (claimed→new
    # token, reclaimed_*→the OLD rotated token, lease_lost/released_*→the presented
    # token), so PG's default NULLS-DISTINCT never defeats this idempotency key.
    op.create_unique_constraint(
        "uq_job_lease_events_job_token_event", "job_lease_events",
        ["job_id", "claim_token", "event_type"])
    op.create_index("ix_job_lease_events_job_id", "job_lease_events", ["job_id"])
    op.create_index("ix_job_lease_events_created_at", "job_lease_events", ["created_at"])

def downgrade() -> None:
    op.drop_index("ix_job_lease_events_created_at", table_name="job_lease_events")
    op.drop_index("ix_job_lease_events_job_id", table_name="job_lease_events")
    op.drop_constraint("uq_job_lease_events_job_token_event", "job_lease_events", type_="unique")
    op.drop_table("job_lease_events")
    op.drop_column("phase_outputs", "claim_token")
    op.drop_column("homework_jobs", "claim_token")
```

- [ ] **Step 4: Add the ORM columns + model.** In `homework_job.py` and `phase_output.py` add (match the file's mapped-column style):

```python
claim_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
```

```python
# app/models/job_lease_event.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class JobLeaseEvent(Base):
    __tablename__ = "job_lease_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    claim_token: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 5: Run migration up on scratch DB + test passes** — `uv run alembic upgrade head` then the Step-1 test → PASS. Confirm single head: `uv run alembic heads` → `0052_job_lease_fencing (head)`.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0052_job_lease_fencing.py app/models/job_lease_event.py app/models/homework_job.py app/models/phase_output.py tests/repositories/test_migration_0052_lease.py
git commit -m "feat(db): migration 0052 — claim_token columns + job_lease_events ledger"
```

---

## Task 2: Lease value types + ledger repo

**Files:**
- Create: `app/services/lease.py`
- Create: `app/repositories/lease_events.py`
- Test: `tests/services/test_lease_types.py`

**Interfaces:**
- Produces:
  - `JobLease(job_id: UUID, claim_token: UUID, owner_id: str)` — frozen dataclass.
  - `ClaimedJob(job: HomeworkJob, lease: JobLease)` — frozen.
  - Singletons `LeaseLost`, `CancelRequested` (module-level sentinel instances).
  - `HeartbeatOutcome` enum: `RENEWED`, `CANCELLING`, `LOST`.
  - Event constants: `EVENT_CLAIMED="claimed"`, `EVENT_RECLAIMED_STALE="reclaimed_stale"`, `EVENT_RECLAIMED_FORCED="reclaimed_forced"`, `EVENT_RELEASED_DONE="released_done"`, `EVENT_RELEASED_RETRY="released_retry"`, `EVENT_RELEASED_FAILED="released_failed"`, `EVENT_RELEASED_CANCELLED="released_cancelled"`, `EVENT_LEASE_LOST="lease_lost"`.
  - `lease_events.append_event(session, *, job_id, claim_token, event_type, owner=None, actor=None, reason=None)` — idempotent (`ON CONFLICT DO NOTHING` on the unique key).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_lease_types.py
import uuid, dataclasses, pytest
from app.services import lease

def test_joblease_is_frozen():
    l = lease.JobLease(job_id=uuid.uuid4(), claim_token=uuid.uuid4(), owner_id="h:1@sha")
    with pytest.raises(dataclasses.FrozenInstanceError):
        l.claim_token = uuid.uuid4()

def test_sentinels_are_distinct_singletons():
    assert lease.LeaseLost is lease.LeaseLost
    assert lease.LeaseLost is not lease.CancelRequested

def test_heartbeat_outcomes_exist():
    assert {lease.HeartbeatOutcome.RENEWED, lease.HeartbeatOutcome.CANCELLING,
            lease.HeartbeatOutcome.LOST}

def test_event_constants():
    assert lease.EVENT_CLAIMED == "claimed"
    assert lease.EVENT_RECLAIMED_FORCED == "reclaimed_forced"
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/services/test_lease_types.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `lease.py`**

```python
# app/services/lease.py
import enum, uuid
from dataclasses import dataclass
from app.models.homework_job import HomeworkJob

@dataclass(frozen=True)
class JobLease:
    job_id: uuid.UUID
    claim_token: uuid.UUID
    owner_id: str

@dataclass(frozen=True)
class ClaimedJob:
    job: HomeworkJob
    lease: JobLease

class _Sentinel:
    __slots__ = ("_name",)
    def __init__(self, name): self._name = name
    def __repr__(self): return f"<{self._name}>"

LeaseLost = _Sentinel("LeaseLost")
CancelRequested = _Sentinel("CancelRequested")

class HeartbeatOutcome(enum.Enum):
    RENEWED = "renewed"
    CANCELLING = "cancelling"
    LOST = "lost"

EVENT_CLAIMED = "claimed"
EVENT_RECLAIMED_STALE = "reclaimed_stale"
EVENT_RECLAIMED_FORCED = "reclaimed_forced"
EVENT_RELEASED_DONE = "released_done"
EVENT_RELEASED_RETRY = "released_retry"
EVENT_RELEASED_FAILED = "released_failed"
EVENT_RELEASED_CANCELLED = "released_cancelled"
EVENT_LEASE_LOST = "lease_lost"
```

- [ ] **Step 4: Implement `lease_events.append_event`**

```python
# app/repositories/lease_events.py
import uuid
from typing import Optional
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.job_lease_event import JobLeaseEvent

async def append_event(session: AsyncSession, *, job_id: uuid.UUID,
                       claim_token: Optional[uuid.UUID], event_type: str,
                       owner: Optional[str] = None, actor: Optional[str] = None,
                       reason: Optional[str] = None) -> None:
    stmt = pg_insert(JobLeaseEvent).values(
        job_id=job_id, claim_token=claim_token, event_type=event_type,
        owner=owner, actor=actor, reason=reason,
    ).on_conflict_do_nothing(constraint="uq_job_lease_events_job_token_event")
    await session.execute(stmt)
```

- [ ] **Step 5: Run tests → PASS.**

- [ ] **Step 6: Commit**

```bash
git add app/services/lease.py app/repositories/lease_events.py tests/services/test_lease_types.py
git commit -m "feat(lease): immutable lease value types + idempotent ledger append"
```

---

## Task 3: Claim mints the token and records the claimed event

**Files:**
- Modify: `app/repositories/jobs.py` (`claim_next_job`)
- Modify: `app/services/worker.py` (`_execute_job` — thread the returned lease)
- Test: `tests/repositories/test_lease_fencing.py` (real-DB)

**Interfaces:**
- Consumes: `lease.ClaimedJob`, `lease.JobLease`, `lease_events.append_event`, `lease.EVENT_CLAIMED`.
- Produces: `claim_next_job(...)` now returns `ClaimedJob | None` (was `HomeworkJob | None`). The claim UPDATE sets `claim_token = :fresh_uuid`; a `claimed` ledger row is written in the same transaction.

**⚠ Locate `claim_next_job` by name; keep its existing `FOR UPDATE SKIP LOCKED` pick + all eligibility WHERE clauses (content/judge/extract/solver capability gates, paused-batch, fleet version gate) unchanged.**

- [ ] **Step 1: Write the failing test**

```python
# tests/repositories/test_lease_fencing.py (real-DB; RUN_DB_INTEGRATION=1)
import os, uuid, pytest
pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="real DB only")

@pytest.mark.asyncio
async def test_claim_mints_token_and_records_event(db_session, seed_pending_job):
    from app.repositories import jobs as jobs_repo
    from app.models.job_lease_event import JobLeaseEvent
    from sqlalchemy import select
    claimed = await jobs_repo.claim_next_job(
        db_session, worker_id="h:1@sha", capabilities=ANY_CAPS, max_attempts=5)
    assert claimed is not None
    assert claimed.lease.claim_token is not None
    assert claimed.job.claim_token == claimed.lease.claim_token
    ev = (await db_session.execute(
        select(JobLeaseEvent).where(JobLeaseEvent.job_id == claimed.job.id))).scalars().all()
    assert any(e.event_type == "claimed" and e.claim_token == claimed.lease.claim_token for e in ev)
```

*(Use the file's existing real-DB fixtures for `db_session`/`seed_pending_job`/`ANY_CAPS`; mirror `tests/integration/test_batch_resume.py` for the scratch-DB setup.)*

- [ ] **Step 2: Run to verify it fails** — token is `None` / return type mismatch.

- [ ] **Step 3: Implement.** In `claim_next_job`, after picking `job_id`:

```python
token = uuid.uuid4()
await session.execute(
    update(HomeworkJob).where(HomeworkJob.id == job_id).values(
        status="running", claimed_at=func.now(), claimed_by=worker_id,
        claim_token=token,
        attempts=HomeworkJob.attempts + 1, last_attempt_at=func.now(),
        started_at=func.now(), error_message=None,
    )
)
await lease_events.append_event(session, job_id=job_id, claim_token=token,
                                event_type=lease.EVENT_CLAIMED, owner=worker_id)
job = await session.get(HomeworkJob, job_id)
return lease.ClaimedJob(job=job, lease=lease.JobLease(job_id=job_id, claim_token=token, owner_id=worker_id))
```

Update `worker._execute_job` to unpack `claimed.job` / `claimed.lease` and pass `lease` down (subsequent tasks consume it). Update the claim-loop call site and any other `claim_next_job` callers/type hints.

- [ ] **Step 4: Run test → PASS.** Also run the existing worker/claim unit tests; fix call sites the return-type change touched.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py app/services/worker.py tests/repositories/test_lease_fencing.py
git commit -m "feat(claim): mint per-claim token, return ClaimedJob, record claimed event"
```

---

## Task 4: Reclaim/requeue rotate the token + registry-liveness cross-check

**Files:**
- Modify: `app/repositories/jobs.py` (`reclaim_stuck_jobs`, `fail_exhausted_pending_jobs`, `reclaim_orphans_on_startup`, `reclaim_stale_cancelling`, `reset_for_retry`, `requeue_session_limited`, `requeue_slot_saturated`)
- Modify: `app/repositories/phase_outputs.py` (`reset_abandoned_phases` clears phase `claim_token`)
- Test: `tests/repositories/test_lease_fencing.py`

**Interfaces:**
- Consumes: the `workers` registry table (ORM `WorkerNode`, `__tablename__="workers"`, PK `pc_id` == `claimed_by`, `last_heartbeat`) for owner-liveness; `lease.EVENT_RECLAIMED_STALE/FORCED`.
- Produces: reclaim/requeue clear `homework_jobs.claim_token` (same UPDATE that clears `claimed_at`) and `phase_outputs.claim_token`; reclaim records `reclaimed_stale`/`reclaimed_forced` **with the OLD (pre-rotation) token**. **Normal reclaim additionally requires the owner process be absent/stale in the `workers` registry**; forced reclaim ignores a live owner only past the hard deadline (`started_at < now - (job_timeout + reclaim_stale)`).
- **Note on `reset_for_retry`:** it is ORM-attribute style (`job.status=...; job.attempts=0`), NOT a `.values(...)` UPDATE, and today does **not** clear `claimed_at`/`claimed_by`. Add `job.claim_token = None` there (and also clear `job.claimed_at = None; job.claimed_by = None` — a retried-from-failed job currently keeps dead claim columns).
- **Note on `reclaim_stale_cancelling`** (`jobs.py`, finalizes a stale mid-cancel `cancelling→cancelled`): it must also `claim_token=None` (+ `claimed_at`/`claimed_by`) in its UPDATE, else a terminal job keeps a live-looking token — violating the "cleared on every reclaim/requeue" invariant.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_reclaim_clears_token_and_records_event_with_OLD_token(db_session, claimed_job_gone_stale):
    from app.repositories import jobs as jobs_repo
    old_token = claimed_job_gone_stale.claim_token          # capture BEFORE reclaim
    ids = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    job = await _reload(db_session, claimed_job_gone_stale.id)
    assert job.status == "pending" and job.claim_token is None
    # the event must carry the OLD token, not the NULL post-update value
    assert await _has_event_with_token(db_session, job.id, "reclaimed_stale", old_token)

@pytest.mark.asyncio
async def test_fresh_registry_owner_blocks_normal_reclaim(db_session, stale_claimed_at_but_live_owner):
    # claimed_at is stale, BUT the owning pc_id still heartbeats the workers registry
    from app.repositories import jobs as jobs_repo
    ids = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert stale_claimed_at_but_live_owner.id not in ids  # not reclaimed — owner alive

@pytest.mark.asyncio
async def test_hard_deadline_forces_reclaim_despite_live_owner(db_session, past_hard_deadline_live_owner):
    from app.repositories import jobs as jobs_repo
    ids = await jobs_repo.reclaim_stuck_jobs(db_session, stale_after_seconds=120)
    assert past_hard_deadline_live_owner.id in ids
    assert await _has_event(db_session, past_hard_deadline_live_owner.id, "reclaimed_forced")
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** In `reclaim_stuck_jobs`, **capture the OLD token before nulling it** — a plain `UPDATE ... SET claim_token=NULL ... RETURNING claim_token` returns the NEW (NULL) value, so use a CTE that snapshots the pre-update row:

```python
# normal (stale) reclaim — owner NOT live in the workers registry
stale_cte = (
    select(HomeworkJob.id, HomeworkJob.claim_token)
    .where(HomeworkJob.status == "running")
    .where((HomeworkJob.claimed_at.is_(None))
           | (HomeworkJob.claimed_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds)))
    .where(~exists(select(WorkerNode.pc_id).where(       # WorkerNode.__tablename__ == "workers"
        WorkerNode.pc_id == HomeworkJob.claimed_by,
        WorkerNode.last_heartbeat >= func.now() - func.make_interval(0, 0, 0, 0, 0, 0, registry_stale_seconds))))
    .with_for_update(skip_locked=True)
).cte("stale")
upd = (update(HomeworkJob).where(HomeworkJob.id.in_(select(stale_cte.c.id)))
       .values(status="pending", claimed_at=None, claimed_by=None, claim_token=None, current_phase=None))
await session.execute(upd)
reclaimed = (await session.execute(select(stale_cte.c.id, stale_cte.c.claim_token))).all()  # old tokens
```

  - **Forced** reclaim is a second matched set: `status='running' AND started_at < now - (:job_timeout + :stale)` (ignores a live owner). Same CTE-snapshot pattern; event type `EVENT_RECLAIMED_FORCED`.
  - For each reclaimed `(id, old_token)`: `reset_abandoned_phases([id], ...)` (already called internally today — keep that) and `append_event(session, job_id=id, claim_token=old_token, event_type=EVENT_RECLAIMED_STALE|FORCED, reason=...)`.
  - Apply `claim_token=None` to the `.values(...)`/attribute writes of `fail_exhausted_pending_jobs`, `reclaim_orphans_on_startup`, `requeue_session_limited`, `requeue_slot_saturated`, and `reclaim_stale_cancelling` (all already null `claimed_at`/`claimed_by`), and to `reset_for_retry` per the Interfaces note (ORM-attribute style: `job.claim_token = None`, plus clear `job.claimed_at`/`job.claimed_by`).
  - In `phase_outputs.reset_abandoned_phases`, add `claim_token=None` to its `.values(...)`.

*(Import `WorkerNode` from `app/models/worker.py`; its `__tablename__` is `"workers"`. `pc_id` = `hostname:pid@sha`, identical to `claimed_by`. Verify `make_interval`'s arg positions against the existing `reclaim_stuck_jobs` call — reuse the exact interval idiom already in the file rather than the illustrative positions above.)*

- [ ] **Step 4: Run tests → PASS.** Re-run the 0155/0156 suites (`test_queue_*`, orphan-reconcile) — they MUST stay green.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py app/repositories/phase_outputs.py tests/repositories/test_lease_fencing.py
git commit -m "feat(reclaim): rotate/clear claim_token on reclaim/requeue + registry-liveness + forced-deadline"
```

---

## Task 5: Fence every worker-owned job write with the token + cancel-wins reconciliation

**Files:**
- Modify: `app/repositories/jobs.py` (`touch_claim`, `set_status`, `mark_failed_with_retry`, `requeue_session_limited`, `requeue_slot_saturated`, `mark_cancelled`, `_finalize_if_cancelling`, and a new `heartbeat_check`)
- Test: `tests/repositories/test_lease_fencing.py`

**Interfaces:**
- Consumes: `lease.JobLease`, `lease.LeaseLost`, `lease.CancelRequested`, `lease.HeartbeatOutcome`, `PhaseOutput` (for the finalize phase sweep), event constants.
- Produces: every worker-owned mutation takes `claim_token: uuid.UUID | None = None` (transitional default per Global Constraints) and, when a token is given, adds `AND claim_token = :token` to its WHERE. On a 0-row match it re-reads `status`+`claim_token` and returns `LeaseLost` (token gone/changed) or `CancelRequested` (same token, `status='cancelling'` — after the repo has internally finalized via `finalize_cancelled`). New: `finalize_cancelled(session, job_id, claim_token) -> CancelRequested|LeaseLost` (idempotent fenced `cancelling→cancelled` + non-done-phase sweep + `released_cancelled`) and `heartbeat_check(session, job_id, claim_token) -> HeartbeatOutcome`.

**⚠ `mark_cancelled` currently has NO status guard (bare `WHERE id`). The operator/admin cancel path must keep working — cancel is initiated by the API without a worker token. Resolution: keep an *unfenced admin cancel* that sets `status='cancelling'` (request), and a *fenced worker finalize* (`finalize_cancelled(token)`) that flips `cancelling→cancelled`. The worker only ever calls the fenced finalize.**

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_stale_token_write_is_noop(db_session, reclaimed_then_reclaimed_job):
    from app.repositories import jobs as jobs_repo
    from app.services import lease
    old = reclaimed_then_reclaimed_job.old_lease   # A's dead token
    res = await jobs_repo.set_status(db_session, old.job_id, "done", claim_token=old.claim_token)
    assert res is lease.LeaseLost
    job = await _reload(db_session, old.job_id)
    assert job.status != "done"       # A did NOT complete B's job

@pytest.mark.asyncio
async def test_cancel_still_wins_over_fenced_requeue(db_session, running_job_being_cancelled):
    from app.repositories import jobs as jobs_repo
    from app.services import lease
    lease_ = running_job_being_cancelled.lease   # CURRENT owner's token
    # user cancel set status='cancelling' out of band
    res = await jobs_repo.mark_failed_with_retry(db_session, lease_.job_id, "boom", claim_token=lease_.claim_token)
    assert res is lease.CancelRequested          # cancel wins, NOT a retry
    job = await _reload(db_session, lease_.job_id)
    assert job.status == "cancelled"

@pytest.mark.asyncio
async def test_heartbeat_check_distinguishes_lost_from_cancelling(db_session, ...):
    from app.repositories import jobs as jobs_repo
    from app.services.lease import HeartbeatOutcome
    assert await jobs_repo.heartbeat_check(db_session, live.job_id, live.token) is HeartbeatOutcome.RENEWED
    assert await jobs_repo.heartbeat_check(db_session, cancelling.job_id, cancelling.token) is HeartbeatOutcome.CANCELLING
    assert await jobs_repo.heartbeat_check(db_session, reclaimed.job_id, reclaimed.old_token) is HeartbeatOutcome.LOST
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement the fenced-write pattern.** Add a shared helper and apply it:

**Single finalize contract (resolves the Task-5/Task-7 double-finalize hazard): the REPO finalizes on `CancelRequested`; the worker treats `CancelRequested` as a pure signal and never finalizes again.** `_fenced_update`, when it detects same-token + `cancelling`, calls `finalize_cancelled` itself (which is idempotent) and returns the signal:

```python
async def finalize_cancelled(session, job_id, claim_token) -> object:
    """Fenced cancelling→cancelled that ALSO fails every non-done phase row —
    matching today's mark_cancelled (jobs.py, the second UPDATE). Idempotent:
    if the job is already cancelled it is a silent no-op (no duplicate event,
    no spurious lease_lost)."""
    row = await session.get(HomeworkJob, job_id, populate_existing=True)
    if row is None or row.status == "cancelled":
        return lease.CancelRequested
    if row.claim_token != claim_token:
        return lease.LeaseLost
    await session.execute(update(HomeworkJob)
        .where(HomeworkJob.id == job_id, HomeworkJob.status == "cancelling")
        .values(status="cancelled", completed_at=func.now(), claim_token=None,
                claimed_at=None, claimed_by=None))
    # phase sweep — the shipped 0155 cancel contract (fail all non-done phases)
    await session.execute(update(PhaseOutput)
        .where(PhaseOutput.job_id == job_id, PhaseOutput.status != "done")
        .values(status="failed", completed_at=func.now(), claim_token=None))
    await lease_events.append_event(session, job_id=job_id, claim_token=claim_token,
                                    event_type=lease.EVENT_RELEASED_CANCELLED)
    return lease.CancelRequested

async def _fenced_update(session, job_id, claim_token, values, *, status_guard, release_event=None) -> object:
    stmt = (update(HomeworkJob)
            .where(HomeworkJob.id == job_id, HomeworkJob.claim_token == claim_token, status_guard)
            .values(**values).returning(HomeworkJob.id))
    hit = (await session.execute(stmt)).scalar_one_or_none()
    if hit is not None:
        if release_event:
            await lease_events.append_event(session, job_id=job_id, claim_token=claim_token, event_type=release_event)
        return hit
    # 0 rows: distinguish lease-lost from cancel-wins (re-read, don't guess)
    row = await session.get(HomeworkJob, job_id, populate_existing=True)
    if row is None or row.claim_token != claim_token:
        await lease_events.append_event(session, job_id=job_id, claim_token=claim_token,
                                        event_type=lease.EVENT_LEASE_LOST)
        return lease.LeaseLost
    if row.status == "cancelling":
        return await finalize_cancelled(session, job_id, claim_token)   # repo finalizes; returns CancelRequested
    return lease.LeaseLost  # token matches but status moved terminal underneath us
```

- `touch_claim(session, job_id, claim_token)`: `_fenced_update(..., values={"claimed_at": func.now()}, status_guard=HomeworkJob.status=="running")`.
- `set_status(...)` (→ `EVENT_RELEASED_DONE` on the `done` transition), `mark_failed_with_retry(...)` (→ `EVENT_RELEASED_FAILED`), `requeue_*(...)` (→ `EVENT_RELEASED_RETRY`): add `claim_token: uuid.UUID | None = None` (transitional default — see Global Constraints), route through `_fenced_update` with the matching `release_event`. **These functions no longer call `_finalize_if_cancelling` directly** — the cancel-wins path is now the `CancelRequested` return from `_fenced_update`'s internal `finalize_cancelled`. (`_finalize_if_cancelling` may be deleted or kept as a thin wrapper the admin paths still use — verify no admin caller regresses.)
- Add `heartbeat_check`: re-read status+token → `LOST` if token gone/changed, `CANCELLING` if `status=='cancelling'`, else `touch_claim` + `RENEWED`. (It does NOT finalize — the worker's normal terminal write finalizes; heartbeat only signals.)
- Keep the **admin/operator** cancel entries (`request_cancel`→sets `cancelling`; `cancel_if_pending`; `cancel_all_in_batch`) **unfenced and behavior-unchanged** — they set `cancelling`/`cancelled-from-pending` and never present a worker token. The fenced `finalize_cancelled` is what the *worker* uses to complete the `cancelling→cancelled` flip that `mark_cancelled` used to do.

- [ ] **Step 4: Run tests → PASS; re-run all 0155 cancel-wins tests → green.**

- [ ] **Step 5: Commit**

```bash
git add app/repositories/jobs.py tests/repositories/test_lease_fencing.py
git commit -m "feat(fence): token-guard all worker job writes; reconcile lease-loss vs cancel-wins"
```

---

## Task 6: Fence phase writes + lease-verify + job-row lock before phase init

**Files:**
- Modify: `app/repositories/phase_outputs.py` (`create_or_reset`, `set_status`)
- Modify: `app/services/pipeline.py` (phase-init call site; thread the lease)
- Test: `tests/repositories/test_phase_lease_fencing.py` (real-DB)

**Interfaces:**
- Consumes: `lease.JobLease`.
- Produces: `create_or_reset(...)` runs under a `SELECT ... FOR UPDATE` on the **job row**, verifies `job.claim_token == lease.claim_token` (else returns `LeaseLost` and writes nothing), and stamps `phase_outputs.claim_token = lease.claim_token`. Phase `set_status`/content writes take `claim_token` and add `AND claim_token = :token`. Completed-phase reuse (the existing `_done_phase_md` path) is unchanged.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_phase_init_under_stale_lease_writes_nothing(db_session, reclaimed_job, dead_lease):
    from app.repositories import phase_outputs as phase_repo
    from app.services import lease
    res = await phase_repo.create_or_reset(db_session, job_id=reclaimed_job.id,
            phase_name="preview", phase_order=0, status="running", lease=dead_lease)
    assert res is lease.LeaseLost
    rows = await phase_repo.list_for_job(db_session, reclaimed_job.id)
    assert all(r.phase_name != "preview" or r.status != "running" for r in rows)

@pytest.mark.asyncio
async def test_two_workers_race_phase_init_one_row_survives(db_session_factory, job):
    # concurrent create_or_reset with the CURRENT lease from two sessions:
    # the FOR UPDATE job-row lock serializes them; exactly one stable phase row.
    ...
    assert count_phase_rows(job.id, "preview") == 1

@pytest.mark.asyncio
async def test_completed_phase_reuse_path_unchanged(db_session, job_with_done_preview):
    # Reuse lives in the PIPELINE, not create_or_reset (which hard-resets any row,
    # incl. done). Assert via the resume helpers: a done+nonempty phase is surfaced
    # by _done_phase_md and excluded from the pending set — so create_or_reset is
    # never even called for it. Do NOT call create_or_reset on a done row here.
    from app.services import pipeline
    done_md = await pipeline._done_phase_md(db_session, job_with_done_preview.id)
    assert done_md.get("preview")                      # done+nonempty is reusable
    pending = pipeline._pending_phases(flow, done_md)   # 'preview' excluded
    assert "preview" not in pending
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** In `create_or_reset`, before the existing SELECT-then-(UPDATE|INSERT):

```python
job = await session.get(HomeworkJob, job_id, with_for_update=True)  # lock the job row FIRST
if job is None or job.claim_token != lease.claim_token:
    return lease.LeaseLost
# ... existing create_or_reset body, but stamp claim_token on the row it writes:
#     existing.claim_token = lease.claim_token   (reset branch)
#     create(..., claim_token=lease.claim_token) (insert branch)
```

Add `claim_token` to phase `set_status` writes (fenced with `AND claim_token = :token`; a mismatch returns `LeaseLost` — caller aborts the phase without mutation). Thread `lease` from `pipeline._execute_phase`/the phase-init site into these calls. Row-lock order is **job then phase** everywhere (matches Task 4).

- [ ] **Step 4: Run tests → PASS.** Re-run the structured/pipeline suites (`test_pipeline_structured.py`, etc.) — green.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/phase_outputs.py app/services/pipeline.py tests/repositories/test_phase_lease_fencing.py
git commit -m "feat(fence): job-row-lock + lease-verify + phase-token on phase writes"
```

---

## Task 7: Worker propagates LeaseLost/CancelRequested; heartbeat uses the token

**Files:**
- Modify: `app/services/worker.py` (`_execute_job`, `_heartbeat`, cancel/finalize)
- Modify: `app/services/pipeline.py` (let the two sentinels pass through the scheduler + broad `except` boundaries without becoming content errors or queue retries)
- Test: `tests/integration/test_reclaim_fencing_e2e.py`

**Interfaces:**
- Consumes: `lease.JobLease`, `HeartbeatOutcome`, `LeaseLost`, `CancelRequested`, `heartbeat_check`.
- Produces: worker behavior — `LeaseLost` anywhere → cancel THIS execution's local task, mutate nothing, log `EVENT_LEASE_LOST` (deduped). `CancelRequested` → cancel THIS execution's local task and stop; **do NOT finalize again** — the repo's fenced write already performed `finalize_cancelled` (cancelled + phase sweep + `released_cancelled`). `_heartbeat` calls `heartbeat_check`; `CANCELLING`→cancel local task; `LOST`→cancel local task, stop heartbeating; DB error → warn + continue (not lost).

- [ ] **Step 1: Write the end-to-end failing test** (real-DB, the design's core scenario)

```python
# tests/integration/test_reclaim_fencing_e2e.py  (RUN_DB_INTEGRATION=1)
@pytest.mark.asyncio
async def test_paused_worker_cannot_mutate_after_reclaim(db_session_factory, seed_job):
    # A claims; A's claimed_at forced stale; B reclaims+claims (new token);
    # A resumes and tries: heartbeat -> LOST; set_status(done) -> LeaseLost (job not done);
    # finalize/ archive -> refused. Assert job still owned by B, not done-by-A.
    ...
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** The claim happens in `_claim_one` (worker.py) which today returns a bare `job.id` to the run loop → `_execute_job(job_id)`. **Plumb the `JobLease` through that hop** (return `ClaimedJob` from `_claim_one`, pass `lease` into `_execute_job`), since every fenced write below needs `lease.claim_token`. In `_execute_job`, wrap the pipeline run so a returned/raised `LeaseLost` cancels `RUNNING_JOBS[job_id]` and returns without any job/phase write; `CancelRequested` just cancels the local task and returns (repo already finalized — no second finalize). Rework `_heartbeat` to `heartbeat_check(session, job_id, lease.claim_token)` (interval unchanged 30s) and act on the enum. Ensure `pipeline` re-raises the two sentinels through its scheduler/`except Exception` guards (they are control signals, not `TransientPhaseError`/content errors).

- [ ] **Step 4: Run e2e test → PASS. Run full non-DB suite → green.**

- [ ] **Step 5: Commit**

```bash
git add app/services/worker.py app/services/pipeline.py tests/integration/test_reclaim_fencing_e2e.py
git commit -m "feat(worker): propagate LeaseLost/CancelRequested; token-aware heartbeat"
```

---

## Task 8: Scope the startup phase reconcile to reclaimed job IDs

**Files:**
- Modify: `main.py` (`lifespan`)
- Test: `tests/api/test_startup_reconcile_scoped.py`

**Interfaces:**
- Consumes: `jobs_repo.reclaim_orphans_on_startup` (returns an **int count**, and already calls `reset_abandoned_phases` internally on the jobs it reclaims via `reclaim_stuck_jobs`).
- Produces: lifespan no longer globally rewrites every `pending`/`running` phase row (`list_running_for_sweep` loop is deleted). The scoped startup reclaim already reconciles the phase rows of the (stale) jobs it reclaims; fresh peer-owned jobs and their phases are untouched. **Behavior change to note in the worklog:** a just-died job whose `claimed_at` is not yet stale is no longer force-failed at boot — it is reclaimed later when it goes stale (≤120s), which is the correct fenced behavior (don't steal a job a live peer might still own).

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_startup_leaves_fresh_peer_owned_rows_untouched(db_session, peer_owned_running_job):
    from main import _reconcile_on_startup   # extract the sweep into a tested fn
    await _reconcile_on_startup(db_session)
    job = await _reload(db_session, peer_owned_running_job.id)
    assert job.status == "running"           # peer's live job not stolen
    phases = await _phases(db_session, job.id)
    assert all(p.status in ("running", "pending", "done") for p in phases)  # not force-failed
```

- [ ] **Step 2: Run to verify it fails** (today's global sweep force-fails the peer's phase rows).

- [ ] **Step 3: Implement.** Extract the lifespan sweep body into `_reconcile_on_startup(session)`. **Delete** the global `for p in list_running_for_sweep(...): set_status(failed)` loop (main.py). Keep the existing `await jobs_repo.reclaim_orphans_on_startup(session)` call — it already resets the reclaimed jobs' phase rows via `reset_abandoned_phases` inside `reclaim_stuck_jobs`, so no separate phase call is needed (and `reclaim_orphans_on_startup` returns an int count, not IDs). Preserve the `ORPHANED_RESTART_MESSAGE` marker semantics that the reclaim path already uses. *(If the wider `("running","pending")` phase source set is genuinely wanted at boot, change `reclaim_orphans_on_startup` to also return the reclaimed IDs and call `reset_abandoned_phases(ids, source_statuses=("running","pending"), include_orphan_failed=True)` — but default to the simpler no-extra-call version above.)*

- [ ] **Step 4: Run test → PASS.**

- [ ] **Step 5: Commit**

```bash
git add main.py tests/api/test_startup_reconcile_scoped.py
git commit -m "feat(startup): scope phase reconcile to reclaimed jobs, drop global sweep"
```

---

## Task 9: Fence automatic Notion archival on the winning token (layered on 0129)

**Files:**
- Modify: `app/services/notion_archive.py` (`archive_job`)
- Test: `tests/services/test_notion_archive_fencing.py`

**Interfaces:**
- Consumes: `homework_jobs.claim_token`.
- Produces: `archive_job(job_id, *, claim_token: uuid.UUID | None = None, force: bool = False)`. **The token fence is OPTIONAL and additive:** when a `claim_token` is presented (the worker/pipeline auto-archive path), publish + pointer-update require `job.status == "done"` AND `job.claim_token == claim_token`. When `claim_token is None` (the three existing operator/batch callers — `api/v1/jobs.py::retry_archive_job` non-force, `api/v1/jobs.py` force-archive, `api/v1/batch.py` batch re-archive), the fence is skipped and behavior falls back to today's guards. The **0129 guard always stays**: `notion_archived_at` idempotency AND the `created_at > prior.created_at` direction guard are unchanged. `archive_job` uses two sessions — the token/status re-check MUST be repeated in the second (pointer-update) session, not only the first.

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_only_winning_token_archives(monkeypatch, db_session, done_job):
    from app.services import notion_archive
    # obsolete token → refused, no publish, no pointer write
    calls = _spy_publish(monkeypatch)
    await notion_archive.archive_job(done_job.id, claim_token=OBSOLETE_TOKEN)
    assert calls == []
    # winning token → publishes once, 0129 created_at guard still applied
    await notion_archive.archive_job(done_job.id, claim_token=done_job.claim_token)
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement.** Add the optional `claim_token` param to `archive_job`. Thread it from the **pipeline done path** (`pipeline.py`, where `notion_archive.archive_job(job_id)` is called right after the job is set `done`) — pass the run's `lease.claim_token`. Leave the three token-less callers (`api/v1/jobs.py::retry_archive_job`, the force-archive endpoint, `api/v1/batch.py` batch re-archive) calling `archive_job(...)` without a token. After the existing `notion_archived_at`/`first_archive`/`created_at` logic, gate publish+pointer-set on: `claim_token is None or (job.status == "done" and job.claim_token == claim_token)`; on a token-present mismatch, log and return (non-fatal), write no pointer. Repeat the check in the second (pointer-update) session. Manual operator re-archive (`force=True`) stays unfenced.

- [ ] **Step 4: Run test → PASS.**

- [ ] **Step 5: Commit**

```bash
git add app/services/notion_archive.py tests/services/test_notion_archive_fencing.py
git commit -m "feat(archive): fence auto-archive on winning token, layered on 0129 created_at guard"
```

---

## Task 10: Deterministic race-suite hardening + RED-proof the unfenced regression

**Files:**
- Modify: `tests/integration/test_reclaim_fencing_e2e.py` (add the remaining scenarios)
- Test: same file

**Interfaces:** none new — this task proves the whole fence bites.

Add real-DB deterministic tests for every scenario the design enumerates that isn't already covered by Tasks 3–9:
- Fresh exact-owner heartbeat blocks normal reclaim (Task 4 has the SQL; assert via the worker heartbeat path).
- Heartbeat loss cancels the correct task, not another job sharing nothing but timing.
- Cancel wins against every retry/requeue path (session-limit, slot-saturation, transient).
- Completed phases survive reclaim and are never regenerated.
- Automatic archive accepts only the winning completion token (cross-check with Task 9).
- Lease events are transactional and idempotent (double-append no-ops).
- Existing admin retry/cancel/manual-rearchive still function.

- [ ] **Step 1: RED-proof — with the fence removed, the paused-worker test FAILS.**

```bash
# temporarily revert the claim_token predicate in _fenced_update, run:
RUN_DB_INTEGRATION=1 DATABASE_URL=... uv run pytest tests/integration/test_reclaim_fencing_e2e.py::test_paused_worker_cannot_mutate_after_reclaim -v
# EXPECT: FAIL (A completes B's job). Restore the predicate; EXPECT: PASS.
```

- [ ] **Step 2: Write the remaining scenario tests (above).**

- [ ] **Step 3: Run the full real-DB suite** — `RUN_DB_INTEGRATION=1 DATABASE_URL=127.0.0.1... uv run pytest tests/ -q`.

- [ ] **Step 4: Run the canonical bar (no flag)** — `uv run pytest tests/ -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_reclaim_fencing_e2e.py
git commit -m "test(lease): deterministic reclaim-fencing race suite + RED-proof of the unfenced regression"
```

---

## Task 11: Whole-branch review + finish

- [ ] **Step 1: Dispatch the final whole-branch code review** (most-capable model) over the full diff `origin/Nggaev-v2..HEAD`. Focus: every worker-owned write is fenced (grep for `update(HomeworkJob)` / `update(PhaseOutput)` and confirm each has a `claim_token` predicate or is an intentional admin/unfenced path with a comment); DB-error-≠-lease-loss holds; cancel-wins preserved; 0129 guard intact; row-lock order job→phase everywhere.
- [ ] **Step 2: Address findings** (fix-loop per SDD).
- [ ] **Step 3: Full suite green** (`uv run pytest tests/ -q`) + real-DB suite green.
- [ ] **Step 4: Rebase-check** — `git fetch origin`; if `Nggaev-v2` moved, rebase, re-run both suites.
- [ ] **Step 5: Finish docs** — worklog entry in `docs/memory/MASTER_MEMORY.md` + `docs/memory/INDEX.md` row (worklog number = next free; re-check the INDEX tail at finish, another session may have taken it); `git mv` this plan into `docs/superpowers/plans/shipped/`; de-stale `docs/HOW_IT_WORKS.md`, `docs/CODE_MAP.md`, `docs/DATABASE.md` (new table + columns + lease lifecycle). Close the ROADMAP item if one exists.
- [ ] **Step 6: Open PR against `Nggaev-v2` — do NOT merge, do NOT deploy, do NOT run the soak.** The PR body states Gate 1 only and points to the Gate 2 section below.

---

## Gate 2 — Deployment & capacity soak (SEPARATE approval; NOT part of Gate-1 execution)

**Do not execute any of this by merging the Gate-1 PR.** This requires explicit operator approval immediately before execution, including a hard spend cap and subject mix.

**Preconditions**
1. Pause new claims; drain to **zero running jobs and zero open model calls**.
2. Pre-flight: assert 0 running jobs carry a NULL `claim_token` after a full head+worker restart on one verified SHA; confirm no stale worker versions; confirm one intended worker process per PC.
3. Apply migration `0052` on the head DB; restart head (auto-stamps version floor); pull+restart every model-calling worker on the same SHA.

**Soak protocol** — one complete wave each at **4, 8, 12**, then two replenished waves at **20, 40** concurrent jobs; advance only after every cohort job finishes AND archives successfully.

**⚠ Credential-cap interaction (load-bearing — resolve before the soak):** post-#115 the fleet shares ONE plain Gemini key with `CREDENTIAL_MAX_CONCURRENT_GEMINI=32`. A 40-concurrent-job wave with multi-phase fan-out will **saturate the limiter and park at 32**, so "40" measures the limiter, not the lease. Either (a) raise the credential cap deliberately for the soak and record it, or (b) interpret the 40-wave against the 32 ceiling. Sustained RPM/TPM at this level is untested (burst ramps ≠ sustained); watch for 429s and slot-wait timeouts (both should park, not fail).

**Stop + park on any:** unexpected lease loss/reclaim, overlapping calls for the same job/phase, a stale-token successful write, duplicate/corrupt phase state, DB pool exhaustion or material heartbeat delay, credential-slot exhaustion or recurring 429s, missing phases, archive drift, or wrong Notion pointer.

**Cost:** the hybrid soak totals **144 homeworks ≈ $193** at the recent $1.34 average — exact subject mix and a hard spend cap require explicit approval **immediately before** execution. 40 concurrent becomes the production ceiling only after both 40-job waves pass.

**Assumptions/rollback:** timing defaults unchanged (30/120/90/1800). Migration columns stay nullable for rollback compatibility, but the cutover requires a **fully drained fleet** so no legacy execution crosses it. **Rolling back to unfenced worker code while claims are open is prohibited** — containment means park + drain first.

---

## Self-Review

**Fable gate-review round (2026-08-06) — corrections folded in:** old-token capture via CTE in reclaim (a plain `RETURNING claim_token` returns the NULL post-update value — Blocker 1); single finalize contract (repo finalizes on `CancelRequested`, worker never double-finalizes — Blocker 2) with the non-done-phase sweep inside `finalize_cancelled` (matches shipped 0155 — Blocker 3); Task 8 corrected (`reclaim_orphans_on_startup` returns an int and already reconciles phases; just delete the global sweep — Blocker 4); `reclaim_stale_cancelling` added to the token-clear set (Mod 5); registry table is `workers`/`WorkerNode` not `worker_nodes` (Mod 6); archive fence made optional + call site is `pipeline.py` done path + three token-less callers preserved (Mod 7); `reset_for_retry` is ORM-attribute style and clears no claim columns today (Mod 8); transitional `claim_token=None` default keeps intermediate commits green (Mod 9); `create_or_reset` does NOT reuse completed phases — that's the pipeline's `_done_phase_md` (Mod 10, factual fix); test paramstyle/`max_attempts`/nulls-distinct minors. Verdict path: NEEDS REWORK → localized text fixes applied, no redesign.

**Spec coverage:** every design section maps to a task — lease schema+ledger→T1; internal types→T2; atomic claim→T3; reclaim/forced/registry-liveness→T4; fence every worker write + cancel-wins→T5; phase upsert-via-lease-verify (kept `create_or_reset` per decision 2)→T6; heartbeat/task-cancel/propagation→T7; scoped startup reconcile→T8; archival fence→T9; deterministic race suite + RED-proof→T10; review+finish→T11; deploy+soak→Gate 2. The design's "raw PG upsert" was intentionally descoped (decision 2); "shared handle" corrected to the existing per-job `RUNNING_JOBS` (T7).

**Placeholder scan:** the `...` in a few real-DB tests marks fixture wiring the implementer copies from the named existing test files (`test_batch_resume.py`); every load-bearing SQL/predicate/return-type is spelled out. No "add error handling"/"TODO" left.

**Type consistency:** `claim_next_job → ClaimedJob`; fenced writes take `claim_token: UUID` and return `hit | LeaseLost | CancelRequested`; `create_or_reset(..., lease: JobLease) → row | LeaseLost`; `heartbeat_check → HeartbeatOutcome`; event constants used verbatim from `lease.py`. Consistent across tasks.
