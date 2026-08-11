"""Acceptance smoke for the batch-launch wave stagger (plan 2026-08-11, Task 8).

Two parts, deliberately split so the expensive half stays at ONE lesson:

  (a) SCHEDULE PROOF + CLAIM-THROUGH — $0, zero model calls.
      Runs the real `launch_batch` against a SCRATCH database, then reads
      `scheduled_at` straight back out and watches `queue_depth()` climb as each
      wave falls due. This is the part that proves a future-stamped row really
      does become claimable on real Postgres, rather than resting on
      pre-existing coverage.

  (b) GENERATION ON A HELD-BACK JOB — one lesson, transport=api, real spend.
      Deliberately NOT a fresh single-lesson launch: a 1-lesson launch is always
      wave 0, offset 0, so `scheduled_at` is never stamped and it would prove
      nothing about this feature. Instead it claims one of part (a)'s LAST-wave
      jobs — a row that was genuinely held back by the claim gate — and runs the
      real pipeline on it.

Run it as a module (this repo has no `[build-system]`, so a by-path run puts
`scripts/` on sys.path[0] and `import app...` fails):

    DATABASE_URL=postgresql+asyncpg://edu:PW@127.0.0.1:5432/edu_scratch_stagger \
    uv run python -m scripts.smoke_launch_stagger

Safety rails, all fatal:
  * DATABASE_URL must be an explicit env var, must contain 127.0.0.1, and must
    NOT name edu_copy (production).
  * `app.config`'s resolved __file__ must live inside this worktree — a git
    worktree has no .env of its own, so `load_dotenv` walks UP to
    ~/Documents/.env and would otherwise aim this at a REMOTE host.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import UUID, uuid4

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── rails ────────────────────────────────────────────────────────────────
_dsn = os.environ.get("DATABASE_URL", "")
if not _dsn:
    sys.exit("REFUSING: set DATABASE_URL explicitly (never inherit it).")
if "127.0.0.1" not in _dsn:
    sys.exit(f"REFUSING: DATABASE_URL must pin 127.0.0.1 (got {_dsn!r}).")
if "edu_copy" in _dsn:
    sys.exit("REFUSING: edu_copy is PRODUCTION. Point at a scratch database.")

import app.config as _cfg  # noqa: E402

if not os.path.abspath(_cfg.__file__).startswith(REPO):
    sys.exit(f"REFUSING: app.config resolved to {_cfg.__file__} (outside {REPO}).")
if "edu_copy" in _cfg.settings.database_url:
    sys.exit("REFUSING: settings.database_url points at PRODUCTION.")

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.repositories import jobs as jobs_repo  # noqa: E402

BOOK_ID = UUID("481be5d8-4c72-4cc0-b2b2-8c6a9fad0f4a")  # history g8 ru, PDF on disk
WAVE_SIZE = 2
INTERVAL = 20  # 20s not 60s purely so the wait is bearable
LESSONS = 6


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


async def seed(prod_dsn: str) -> list[UUID]:
    """Copy one book + LESSONS toc rows + launch_defaults from production
    (READ-ONLY there) into the scratch DB. Returns the toc ids in TOC order."""
    import asyncpg

    src = await asyncpg.connect(prod_dsn)
    try:
        book = await src.fetchrow(
            "select subject, grade, source_language, status, original_filename,"
            " content_sha256, file_size_bytes from books where id=$1", BOOK_ID)
        rows = await src.fetch(
            "select id, section_number, section_title, page_start, page_end,"
            " order_index from toc_entries where book_id=$1"
            " order by order_index limit $2", BOOK_ID, LESSONS)
    finally:
        await src.close()

    if book is None or not rows:
        sys.exit("seed: source book/toc rows not found in production")

    async with SessionLocal() as s:
        await s.execute(text(
            "insert into books (id, subject, grade, source_language, status,"
            " original_filename, content_sha256, file_size_bytes, created_at, updated_at)"
            " values (:id,:sub,:g,:lang,:st,:fn,:sha,:sz, now(), now())"
            " on conflict (id) do nothing"), {
                "id": BOOK_ID, "sub": book["subject"], "g": book["grade"],
                "lang": book["source_language"], "st": book["status"],
                "fn": book["original_filename"], "sha": book["content_sha256"],
                "sz": book["file_size_bytes"]})
        for r in rows:
            await s.execute(text(
                "insert into toc_entries (id, book_id, section_number, section_title,"
                " page_start, page_end, order_index, created_at, updated_at)"
                " values (:id,:b,:n,:t,:ps,:pe,:oi, now(), now())"
                " on conflict (id) do nothing"), {
                    "id": r["id"], "b": BOOK_ID, "n": r["section_number"],
                    "t": r["section_title"], "ps": r["page_start"],
                    "pe": r["page_end"], "oi": r["order_index"]})
        await s.commit()
    # launch_defaults is NOT seeded here: migration 0051 already writes the
    # singleton row, and it carries the same live values production uses
    # (extract gemini/3.5-flash-lite, judge gemini/3.5-flash, solver
    # gemini/3.1-pro-preview). Verify rather than overwrite.
    async with SessionLocal() as s:
        ld = (await s.execute(text(
            "select extract_provider, extract_model from launch_defaults"))).first()
    if ld is None:
        sys.exit("seed: launch_defaults singleton missing — migration incomplete")
    _log(f"launch_defaults (from migration): extract={ld[0]}/{ld[1]}")
    _log(f"seeded book {BOOK_ID} + {len(rows)} toc rows into scratch")
    return [r["id"] for r in rows]


async def part_a(toc_ids: list[UUID]) -> UUID:
    """Schedule proof + claim-through. Returns a LAST-wave job id for part (b)."""
    from app.api.v1.batch import BatchLaunchRequest, launch_batch

    _cfg.settings.batch_launch_wave_size = WAVE_SIZE
    _cfg.settings.batch_launch_wave_interval_seconds = INTERVAL

    async with SessionLocal() as s:
        body = BatchLaunchRequest(
            book_id=BOOK_ID, transport="api", provider="gemini",
            model="gemini-3.6-flash", toc_entry_ids=toc_ids)
        payload = await launch_batch(body, s)

    made_claimable = payload["jobs_created"] + payload["jobs_resumed"]
    _log(f"launched: created={payload['jobs_created']} resumed={payload['jobs_resumed']} "
         f"stagger={payload['stagger']}")
    # Assert on created+resumed, not created alone: on a re-run the prior run's
    # cancelled rows are RESUMED instead of recreated. That is the in-launch
    # resume branch, and it must stagger identically — both branches feed the
    # one shared `launched` counter, so either path must yield the same waves.
    assert made_claimable == LESSONS, payload
    assert payload["stagger"]["jobs_launched"] == LESSONS, payload["stagger"]
    assert payload["stagger"]["waves"] == 3, payload["stagger"]

    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "select j.id, t.order_index,"
            " round(extract(epoch from (j.scheduled_at - now())))::int as due_in"
            " from homework_jobs j join toc_entries t on t.id=j.toc_entry_id"
            " where j.book_id=:b order by t.order_index"), {"b": BOOK_ID})).all()
    for r in rows:
        _log(f"  lesson#{r.order_index}  due_in={r.due_in:>3}s  job={str(r.id)[:8]}")

    buckets = sorted({max(r.due_in, 0) for r in rows})
    assert len(buckets) == 3, f"expected 3 distinct waves, got {buckets}"
    _log(f"PASS(a1): {len(buckets)} waves at ~{buckets}s, {WAVE_SIZE} jobs each")

    async with SessionLocal() as s:
        d0 = await jobs_repo.queue_depth(s)
    _log(f"queue_depth now = {d0} (only wave 0 is claimable)")
    assert d0 == WAVE_SIZE, f"expected {WAVE_SIZE}, got {d0}"

    for wave in (1, 2):
        await asyncio.sleep(INTERVAL + 2)
        async with SessionLocal() as s:
            d = await jobs_repo.queue_depth(s)
        expect = WAVE_SIZE * (wave + 1)
        _log(f"after wave {wave} fell due: queue_depth = {d} (expect {expect})")
        assert d == expect, f"expected {expect}, got {d}"
    _log("PASS(a2): each future-stamped wave BECAME claimable on real Postgres")

    last = [r for r in rows if r.due_in >= INTERVAL * 2 - 2]
    return last[0].id


async def part_b(job_id: UUID) -> None:
    """Real generation on a job the stagger genuinely held back."""
    from app.services.pipeline import run as pipeline_run

    # Leave ONLY the held-back job claimable. The claim gate orders by TOC
    # order_index, so without this it would hand back a wave-0 job — one that
    # was never stamped, which would prove nothing. Cancelling the rest makes
    # the assertion below exact: the job we generate is provably the one the
    # stagger pushed into the future.
    async with SessionLocal() as s:
        await s.execute(text(
            "update homework_jobs set status='cancelled'"
            " where status='pending' and id <> :keep"), {"keep": job_id})
        await s.commit()

    async with SessionLocal() as s:
        claimed = await jobs_repo.claim_next_job(
            s, worker_id="smoke-stagger", max_attempts=3, capabilities={
                "can_gemini_api": True, "can_claude_api": True,
                "judge_api_ok": True, "judge_fallback_api_ok": True,
                "extract_api_ok": True})
        await s.commit()
    if claimed is None:
        sys.exit("part(b): the held-back job never became claimable")
    jid = claimed.job.id
    assert jid == job_id, f"claimed {jid}, expected the held-back {job_id}"
    _log(f"claimed the HELD-BACK job {str(jid)[:8]} (was stamped +{INTERVAL * 2}s)")

    _log("running the real pipeline (transport=api) — this bills a credential")
    await pipeline_run(jid, claimed.lease)

    async with SessionLocal() as s:
        st = (await s.execute(text(
            "select status, error_message from homework_jobs where id=:i"),
            {"i": jid})).one()
        n = (await s.execute(text(
            "select count(*) from phase_outputs where job_id=:i and status='done'"),
            {"i": jid})).scalar_one()
    # A REAL assertion. The first version of this logged "PASS" on
    # status=failed/done_phases=0, which asserted nothing — the exact vacuous
    # check this project's review discipline exists to catch.
    if st.status != "done" or n == 0:
        sys.exit(f"FAIL(b): job status={st.status} done_phases={n} "
                 f"error={st.error_message!r}")
    _log(f"PASS(b): job status={st.status} done_phases={n} — generated from a "
         f"row the stagger had held back")


async def report_cost() -> None:
    async with SessionLocal() as s:
        row = (await s.execute(text(
            "select count(*) calls, coalesce(sum(prompt_tokens),0) p,"
            " coalesce(sum(cached_tokens),0) c, coalesce(sum(output_tokens),0) o"
            " from agent_usages"))).one()
    _log(f"SPEND BASIS: calls={row.calls} prompt={row.p} cached={row.c} output={row.o}")
    from app.services import pricing
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "select provider, model_name, prompt_tokens, cached_tokens,"
            " output_tokens, cache_creation_tokens, auth_mode from agent_usages"))).all()
    # cost_usd's real signature is (provider, model, usage_dict) — NOT kwargs.
    # Getting this wrong once made every row silently "skip" and report $0.00,
    # which is the worst possible failure mode for a cost report.
    total = 0.0
    per: dict[str, float] = {}
    for r in rows:
        c = pricing.cost_usd(r.provider, r.model_name, {
            "prompt_tokens": r.prompt_tokens, "cached_tokens": r.cached_tokens,
            "output_tokens": r.output_tokens,
            "cache_creation_tokens": r.cache_creation_tokens or 0,
            "auth_mode": r.auth_mode})
        total += c
        per[r.model_name or "?"] = per.get(r.model_name or "?", 0.0) + c
    for model, c in sorted(per.items(), key=lambda kv: -kv[1]):
        _log(f"  {model:<26} ${c:.4f}")
    _log(f"TOTAL SMOKE SPEND: ${total:.4f}  ({len(rows)} calls)")


async def cleanup() -> None:
    async with SessionLocal() as s:
        await s.execute(text(
            "update homework_jobs set status='cancelled' "
            "where status in ('pending','running','cancelling')"))
        await s.commit()
    _log("cleanup: all non-terminal scratch jobs cancelled")


async def wipe() -> None:
    """Reset job state so every run is deterministic. Scratch DB only — the
    rails at import time have already refused to run against edu_copy.

    Without this, a re-run finds the previous run's rows and takes a DIFFERENT
    launcher branch: pending rows are SKIPPED, cancelled rows are RESUMED. Both
    behaved correctly when observed (resume staggered identically; skip
    consumed no wave slot and reported waves=1), but neither exercises the
    create path this smoke is meant to prove."""
    async with SessionLocal() as s:
        await s.execute(text("delete from agent_usages"))
        await s.execute(text("delete from phase_outputs"))
        await s.execute(text("delete from job_lease_events"))
        await s.execute(text("delete from homework_jobs"))
        await s.execute(text("delete from batches"))
        await s.commit()
    _log("wiped scratch job state (deterministic re-run)")


async def main() -> None:
    prod = os.environ.get("PROD_DSN_READONLY")
    if not prod:
        sys.exit("set PROD_DSN_READONLY (used READ-ONLY, to copy one book's metadata)")
    await wipe()
    toc_ids = await seed(prod)
    target = await part_a(toc_ids)
    if os.environ.get("SMOKE_GENERATE") == "1":
        await part_b(target)
    else:
        _log("SKIPPING part (b) — set SMOKE_GENERATE=1 to spend real money")
    await report_cost()
    await cleanup()


if __name__ == "__main__":
    asyncio.run(main())
