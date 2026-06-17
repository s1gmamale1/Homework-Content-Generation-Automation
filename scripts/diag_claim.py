"""Diagnostic: print the worker's runtime CAPABILITIES and dry-run the
claim_next_job pick predicate (read-only) to see whether our 4 jobs are
claim-eligible. Run with the app importable + .env present."""
import asyncio
import sys

sys.path.insert(0, ".")

from app.services import worker  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import HomeworkJob  # noqa: E402
from sqlalchemy import select, and_, or_, not_, literal, func  # noqa: E402


async def main():
    caps = worker.CAPABILITIES
    print("RUNTIME CAPABILITIES:", caps)

    # Rebuild the same predicates as jobs_repo.claim_next_job (read-only).
    judge_pair = caps.get("judge_pair") or (None, None)
    content_ok = or_(
        HomeworkJob.transport == "cli",
        and_(HomeworkJob.provider == "claude", literal(bool(caps.get("can_claude_api")))),
        and_(HomeworkJob.provider == "gemini", literal(bool(caps.get("can_gemini_api")))),
    )
    judge_needs_api = or_(
        HomeworkJob.judge_transport == "api",
        and_(HomeworkJob.judge_transport == "inherit", HomeworkJob.transport == "api"),
    )
    job_is_judge_pair = and_(
        HomeworkJob.provider == (judge_pair[0] or ""),
        func.coalesce(HomeworkJob.model, "") == (judge_pair[1] or ""),
    )
    judge_ok = or_(
        not_(judge_needs_api),
        and_(job_is_judge_pair, literal(bool(caps.get("judge_fallback_api_ok")))),
        and_(not_(job_is_judge_pair), literal(bool(caps.get("judge_api_ok")))),
    )
    extract_needs_api = or_(
        HomeworkJob.extract_transport == "api",
        and_(HomeworkJob.extract_transport == "inherit", HomeworkJob.transport == "api"),
    )
    extract_ok = or_(not_(extract_needs_api), literal(bool(caps.get("extract_api_ok"))))

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(
                HomeworkJob.id, HomeworkJob.model, HomeworkJob.status,
                content_ok.label("content_ok"),
                judge_ok.label("judge_ok"),
                extract_ok.label("extract_ok"),
            ).where(HomeworkJob.status == "pending")
        )).all()
        print(f"\nPENDING jobs ({len(rows)}):")
        for r in rows:
            print(f"  {str(r.id)[:8]} {r.model:<32} content={r.content_ok} judge={r.judge_ok} extract={r.extract_ok}")


asyncio.run(main())
