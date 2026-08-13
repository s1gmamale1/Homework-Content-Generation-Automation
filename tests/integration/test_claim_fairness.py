"""Real-DB proof: the claim queue spreads scarce capacity over every batch.

THE INCIDENT (measured in production 2026-08-12, fleet-claim-fairness-1)
-----------------------------------------------------------------------
72 jobs claimable, 0 deferred, 38 live workers (152 configured slots) — and
only 37 jobs running, 25% utilisation, with 14 workers holding ZERO. Claiming
was lopsided by book: one batch had 24 running while another had 2 running
against 19 pending.

`claim_next_job` ranked the whole queue by `priority DESC, toc_entries.
order_index ASC, scheduled_at ASC`. `order_index` is a per-BOOK TOC position,
so ranking on it across books is not a meaningful comparison — it just points
every worker in the fleet at one contiguous head of the queue. Whenever
claimable work exceeds what the fleet can hold at once, ALL of the capacity
lands on the batches occupying that head and every other batch gets zero share
however many workers are free.

`test_scarce_capacity_spreads_across_batches` is the reproduction: it is RED on
the pre-fix ordering (2 of 6 batches take all 24 slots, 4 batches run zero with
48 rows still pending) and GREEN on the lane-rank ordering (4 slots each).

The rest of the file is the correctness fence around that change: the fair
ordering must not cost us the no-double-claim guarantee, the lease/fencing
ledger, the within-batch lesson order, or any eligibility gate.

Run:
  createdb claim_scratch
  DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/claim_scratch \\
    uv run alembic upgrade head
  RUN_DB_INTEGRATION=1 \\
    DATABASE_URL=postgresql+asyncpg://edu:edu@127.0.0.1:5432/claim_scratch \\
    uv run python -m pytest tests/integration/test_claim_fairness.py -q
"""
from __future__ import annotations

import asyncio
import os
from collections import Counter

import pytest
from sqlalchemy import delete, select, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION") != "1",
    reason="needs a real Postgres; set RUN_DB_INTEGRATION=1 + DATABASE_URL",
)

# Attempts fence: the integration DB may have LIVE workers polling it (they
# claim with max_attempts=settings.queue_max_attempts). Seeding at attempts=7
# and claiming with max_attempts=8 keeps our rows invisible to them, and the
# high priority keeps foreign rows from sorting ahead of ours. The priority is
# deliberately above the other integration files' fence (1000) so leftover rows
# from a previous run cannot interleave with the ordering under test.
_FENCE_ATTEMPTS = 7
_FENCE_MAX = 8
_FENCE_PRIORITY = 5000
# How many times a claimer may hit a FOREIGN pending row (rolled back, never
# mutated) before giving up. Only matters on a shared/dirty integration DB.
_FOREIGN_RETRIES = 25


# ─── seeding helpers ─────────────────────────────────────────────────────────


async def _seed_batches(n_batches: int, lessons_per_batch: int, *, tag: str):
    """One book + one batch per lane, `lessons_per_batch` pending jobs in each.

    Each book's TOC entries occupy a DIFFERENT `order_index` window (book b
    covers b*L .. b*L+L-1), which is what a real fleet looks like: batches
    launch different slices of different textbooks. Under a global order_index
    ranking that alone decides which batch the whole fleet drains.
    """
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.toc_entry import TOCEntry
    from app.repositories import jobs as jobs_repo

    ids: dict = {"books": [], "batches": [], "jobs_by_batch": {}}
    async with SessionLocal() as s:
        for b in range(n_batches):
            book = Book(
                subject="math-algebra",
                original_filename=f"{tag}-book-{b}.pdf",
                content_sha256=f"{abs(hash((tag, b))):064d}"[:64],
                file_size_bytes=1,
                status="toc_ready",
            )
            s.add(book)
            await s.flush()
            batch = Batch(book_id=book.id, subject="math-algebra",
                          provider="claude", transport="cli")
            s.add(batch)
            await s.flush()
            ids["books"].append(book.id)
            ids["batches"].append(batch.id)
            ids["jobs_by_batch"][batch.id] = []
            for i in range(lessons_per_batch):
                toc = TOCEntry(
                    book_id=book.id,
                    section_title=f"L{i}",
                    # distinct per-book order_index window
                    order_index=b * lessons_per_batch + i,
                )
                s.add(toc)
                await s.flush()
                job = await jobs_repo.create(
                    s, book_id=book.id, toc_entry_id=toc.id,
                    subject="math-algebra", output_language="uz",
                    transport="cli", batch_id=batch.id,
                )
                job.attempts = _FENCE_ATTEMPTS
                job.priority = _FENCE_PRIORITY
                await s.flush()
                ids["jobs_by_batch"][batch.id].append(job.id)
        await s.commit()
    # Pin every scheduled_at into the past so the wave stagger cannot mask the
    # ordering under test.
    async with SessionLocal() as s:
        all_ids = [j for v in ids["jobs_by_batch"].values() for j in v]
        await s.execute(
            text("UPDATE homework_jobs SET scheduled_at = now() - interval '1 hour' "
                 "WHERE id = ANY(:ids)"),
            {"ids": all_ids},
        )
        await s.commit()
    return ids


async def _cleanup(ids: dict):
    from app.db import SessionLocal
    from app.models.batch import Batch
    from app.models.book import Book
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.models.toc_entry import TOCEntry

    all_jobs = [j for v in ids["jobs_by_batch"].values() for j in v]
    async with SessionLocal() as s:
        await s.execute(delete(JobLeaseEvent).where(JobLeaseEvent.job_id.in_(all_jobs)))
        await s.execute(delete(HomeworkJob).where(HomeworkJob.book_id.in_(ids["books"])))
        await s.execute(delete(TOCEntry).where(TOCEntry.book_id.in_(ids["books"])))
        await s.execute(delete(Batch).where(Batch.book_id.in_(ids["books"])))
        await s.execute(delete(Book).where(Book.id.in_(ids["books"])))
        await s.commit()


def _cli_caps() -> dict:
    from app.services.worker import _compute_capabilities

    return _compute_capabilities({})


async def _claim(worker_id: str, own: set, *, barrier: asyncio.Barrier | None = None):
    """One claim in its own session/transaction, exactly like `Worker._claim_one`.

    A foreign row (leftover/live data on a shared integration DB) is ROLLED BACK
    — the test never mutates anything it did not seed — and retried, so foreign
    residue cannot masquerade as the starvation under test.
    """
    from app.db import SessionLocal
    from app.repositories import jobs as jobs_repo

    if barrier is not None:
        await barrier.wait()   # release every claimer in the same instant
    for _ in range(_FOREIGN_RETRIES):
        async with SessionLocal() as s:
            claimed = await jobs_repo.claim_next_job(
                s, worker_id=worker_id, max_attempts=_FENCE_MAX, capabilities=_cli_caps()
            )
            if claimed is None:
                await s.commit()
                return None
            job_id = claimed.job.id
            if job_id in own:
                await s.commit()
                return job_id
            await s.rollback()   # foreign row: untouched, try again
    return None


async def _running_per_batch(ids: dict) -> dict:
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(HomeworkJob.batch_id, HomeworkJob.status)
            .where(HomeworkJob.batch_id.in_(ids["batches"]))
        )).all()
    out = {b: 0 for b in ids["batches"]}
    for batch_id, status in rows:
        if status == "running":
            out[batch_id] += 1
    return out


# ─── THE REPRODUCTION ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scarce_capacity_spreads_across_batches():
    """RED before the fix / GREEN after.

    6 batches x 12 claimable lessons = 72 rows; the fleet can hold 24 at once
    (the incident's shape: far more claimable work than instantaneous
    capacity). Every batch must get a share.

    Pre-fix the global `order_index` ranking hands all 24 slots to the two
    batches whose TOC window sorts first and leaves 4 batches at ZERO running
    with 48 rows pending — the "one batch 24 running, another 2 running against
    19 pending" the incident reported.
    """
    n_batches, per_batch, capacity = 6, 12, 24
    ids = await _seed_batches(n_batches, per_batch, tag="fair")
    own = {j for v in ids["jobs_by_batch"].values() for j in v}
    try:
        got = await asyncio.gather(*[
            _claim(f"fairhost{i:02d}:1", own) for i in range(capacity)
        ])
        claimed = [g for g in got if g is not None]
        assert len(claimed) == capacity, (
            f"only {len(claimed)}/{capacity} claimers got a job while "
            f"{n_batches * per_batch} rows were claimable"
        )

        per = await _running_per_batch(ids)
        starved = [b for b, n in per.items() if n == 0]
        assert not starved, (
            f"{len(starved)} of {n_batches} batches got ZERO of the {capacity} "
            f"claimed slots while {n_batches * per_batch - capacity} rows were "
            f"still pending — the queue drains one batch instead of spreading "
            f"across the fleet. Per-batch running: {sorted(per.values())}"
        )
        # Round robin, not merely non-zero. The bound is deliberately loose
        # (2x the fair share) because genuinely concurrent claimers rank from
        # slightly different snapshots and SKIP LOCKED can push two of them into
        # the same lane — benign jitter, not unfairness. Pre-fix this was 12 (a
        # batch's ENTIRE supply), so the bound still bites hard.
        # `test_serial_claim_order_is_round_robin` pins the exact rotation.
        fair_share = capacity // n_batches
        assert max(per.values()) <= 2 * fair_share, (
            f"one batch took {max(per.values())} of {capacity} slots — more than "
            f"twice its fair share of {fair_share}: {sorted(per.values())}"
        )
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_serial_claim_order_is_round_robin_across_batches():
    """The exact rotation, with concurrency removed so it is deterministic.

    Claiming one at a time from 4 batches must walk batch A, B, C, D, then come
    back for each batch's second lesson — never A's whole book first.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    n_batches, per_batch = 4, 3
    ids = await _seed_batches(n_batches, per_batch, tag="rr")
    own = {j for v in ids["jobs_by_batch"].values() for j in v}
    try:
        order = []
        for i in range(n_batches * per_batch):
            jid = await _claim(f"rrhost{i}:1", own)
            assert jid is not None, f"claim {i} came back empty"
            order.append(jid)
        async with SessionLocal() as s:
            batch_of = dict((await s.execute(
                select(HomeworkJob.id, HomeworkJob.batch_id).where(HomeworkJob.id.in_(own))
            )).all())
        lanes = [batch_of[j] for j in order]
        for round_no in range(per_batch):
            window = lanes[round_no * n_batches:(round_no + 1) * n_batches]
            assert len(set(window)) == n_batches, (
                f"round {round_no} of the rotation repeated a batch instead of "
                f"visiting all {n_batches}: {window}"
            )
    finally:
        await _cleanup(ids)


# ─── correctness fence around the new ordering ───────────────────────────────


@pytest.mark.asyncio
async def test_no_double_claim_under_synchronised_herd():
    """Regression guard: N claimers released in the SAME instant against M rows.

    Every claimer must win a DISTINCT row (SKIP LOCKED still walks past the
    rows its peers are locking), no row may be claimed twice, and the fenced
    lease ledger must carry exactly one `claimed` event per claimed job.
    """
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.job_lease_event import JobLeaseEvent
    from app.services import lease

    n_batches, per_batch = 6, 8      # M = 48 rows
    n_claimers = 48                  # N == M: everyone must win
    ids = await _seed_batches(n_batches, per_batch, tag="herd")
    own = {j for v in ids["jobs_by_batch"].values() for j in v}
    try:
        barrier = asyncio.Barrier(n_claimers)
        got = await asyncio.gather(*[
            _claim(f"herdhost{i:02d}:1", own, barrier=barrier) for i in range(n_claimers)
        ])
        winners = [g for g in got if g is not None]

        assert len(winners) == len(set(winners)), (
            "the SAME job was handed to two claimers: "
            f"{[j for j, c in Counter(winners).items() if c > 1]}"
        )
        assert len(winners) == n_claimers, (
            f"{n_claimers - len(winners)} claimers came back empty-handed while "
            f"rows were still claimable (M={len(own)})"
        )

        async with SessionLocal() as s:
            statuses = Counter((await s.execute(
                select(HomeworkJob.status).where(HomeworkJob.id.in_(own))
            )).scalars().all())
            events = Counter((await s.execute(
                select(JobLeaseEvent.job_id)
                .where(JobLeaseEvent.job_id.in_(own),
                       JobLeaseEvent.event_type == lease.EVENT_CLAIMED)
            )).scalars().all())
            tokens = (await s.execute(
                select(HomeworkJob.claim_token)
                .where(HomeworkJob.id.in_(winners))
            )).scalars().all()

        assert statuses["running"] == n_claimers, f"statuses: {dict(statuses)}"
        dup_events = [j for j, c in events.items() if c != 1]
        assert not dup_events, f"jobs with != 1 `claimed` ledger event: {dup_events}"
        assert all(t is not None for t in tokens), "a claimed job has no claim_token"
        assert len(set(tokens)) == len(tokens), "two claims minted the SAME claim_token"
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_within_a_batch_order_is_still_ascending_lesson_order():
    """The fair-share rank sorts BETWEEN batches; inside one batch the original
    contract (ascending toc_entries.order_index) is unchanged."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob
    from app.models.toc_entry import TOCEntry

    ids = await _seed_batches(1, 6, tag="order")
    own = set(ids["jobs_by_batch"][ids["batches"][0]])
    try:
        seen = []
        for i in range(6):
            jid = await _claim(f"orderhost{i}:1", own)
            assert jid is not None, f"claim {i} came back empty"
            seen.append(jid)
        async with SessionLocal() as s:
            order_by_job = dict((await s.execute(
                select(HomeworkJob.id, TOCEntry.order_index)
                .join(TOCEntry, TOCEntry.id == HomeworkJob.toc_entry_id)
                .where(HomeworkJob.id.in_(own))
            )).all())
        got = [order_by_job[j] for j in seen]
        assert got == sorted(got), f"within-batch lesson order broken: {got}"
    finally:
        await _cleanup(ids)


@pytest.mark.asyncio
async def test_fair_ordering_still_honours_the_eligibility_gates():
    """The lane rank must not smuggle an ineligible row to the head of the
    queue: a future `scheduled_at`, an exhausted `attempts`, and a paused batch
    all stay unclaimable even when their lane would otherwise be next up."""
    from app.db import SessionLocal
    from app.models.homework_job import HomeworkJob

    ids = await _seed_batches(3, 2, tag="gates")
    b_future, b_exhausted, b_paused = ids["batches"]
    own = {j for v in ids["jobs_by_batch"].values() for j in v}
    try:
        async with SessionLocal() as s:
            await s.execute(
                text("UPDATE homework_jobs SET scheduled_at = now() + interval '1 hour' "
                     "WHERE batch_id = :b"), {"b": b_future})
            await s.execute(
                text("UPDATE homework_jobs SET attempts = :a WHERE batch_id = :b"),
                {"a": _FENCE_MAX, "b": b_exhausted})
            # `batches.paused_at` is TIMESTAMP WITHOUT TIME ZONE — set it in SQL
            # rather than binding an aware Python datetime.
            await s.execute(
                text("UPDATE batches SET paused_at = now() WHERE id = :b"),
                {"b": b_paused})
            await s.commit()

        # Drain: only rows that pass every gate may ever be handed out.
        claimed = []
        for i in range(8):
            jid = await _claim(f"gatehost{i}:1", own)
            if jid is None:
                break
            claimed.append(jid)
        assert claimed == [], (
            "a gated job was claimed: every seeded batch is blocked "
            f"(future schedule / attempts exhausted / batch paused) but got {claimed}"
        )

        # And unblocking one lane makes exactly that lane claimable again.
        async with SessionLocal() as s:
            await s.execute(
                text("UPDATE homework_jobs SET scheduled_at = now() - interval '1 hour' "
                     "WHERE batch_id = :b"), {"b": b_future})
            await s.commit()
        jid = await _claim("gatehost-final:1", own)
        assert jid in ids["jobs_by_batch"][b_future], (
            "unblocking the future-scheduled lane did not make it claimable"
        )
        async with SessionLocal() as s:
            assert (await s.get(HomeworkJob, jid)).status == "running"
    finally:
        await _cleanup(ids)
