"""Repository for the launch_defaults singleton (id=1, seeded by migration
0037_launch_defaults). Read once per launch/upload — not a hot path."""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.launch_defaults import LaunchDefaults

_MUTABLE = (
    "judge_provider", "judge_model", "judge_transport",
    "extract_provider", "extract_model", "extract_transport",
    "toc_transport",
    "output_language",
)


async def get(session: AsyncSession) -> LaunchDefaults:
    """Return the singleton (id=1). Raises if missing (broken migration state)."""
    row = await session.get(LaunchDefaults, 1)
    if row is None:
        raise RuntimeError(
            "launch_defaults singleton (id=1) is missing — run 'alembic upgrade head'"
        )
    return row


async def update(session: AsyncSession, fields: dict) -> LaunchDefaults:
    """Partial update of the singleton; touches updated_at. Ignores unknown keys."""
    values = {k: v for k, v in fields.items() if k in _MUTABLE}
    if values:
        values["updated_at"] = func.now()
        await session.execute(
            sa_update(LaunchDefaults).where(LaunchDefaults.id == 1).values(**values)
        )
    return await get(session)
