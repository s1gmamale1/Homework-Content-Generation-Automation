"""Re-run the Notion archive for every `done` job that was never archived.

One-off operational tool. `archive_job` is idempotent (it no-ops on jobs that
already have notion_archived_at) and best-effort, so this is safe to re-run:
resolvable jobs get archived, the rest get a notion_skip_reason. Not wired into
startup. Run: .\\.venv\\Scripts\\python.exe -m scripts.rearchive_unarchived
"""
import asyncio

from sqlalchemy import select

from app.db import SessionLocal
from app.models import HomeworkJob
from app.services.notion_archive import archive_job


async def main() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(HomeworkJob.id).where(
                    HomeworkJob.status == "done",
                    HomeworkJob.notion_archived_at.is_(None),
                )
            )
        ).scalars().all()
    print(f"re-archiving {len(rows)} done+unarchived job(s)")
    for job_id in rows:
        await archive_job(job_id)
        print(f"  processed {job_id}")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
