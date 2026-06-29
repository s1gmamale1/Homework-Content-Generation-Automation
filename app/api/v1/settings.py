from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import launch_defaults as launch_defaults_repo
from app.services.agent_models import api_supported, is_valid, validate_output_language, validate_role_transport

router = APIRouter(tags=["settings"])


class LaunchDefaultsOut(BaseModel):
    judge_provider: str | None
    judge_model: str | None
    judge_transport: str | None
    extract_provider: str | None
    extract_model: str | None
    extract_transport: str | None
    toc_transport: str | None
    output_language: str | None
    content_provider: str | None
    content_model: str | None
    content_transport: str | None


class LaunchDefaultsUpdate(BaseModel):
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_transport: str | None = None
    extract_provider: str | None = None
    extract_model: str | None = None
    extract_transport: str | None = None
    toc_transport: str | None = None
    output_language: str | None = None
    content_provider: str | None = None
    content_model: str | None = None
    content_transport: str | None = None


def _serialize(row) -> LaunchDefaultsOut:
    return LaunchDefaultsOut(
        judge_provider=row.judge_provider, judge_model=row.judge_model,
        judge_transport=row.judge_transport, extract_provider=row.extract_provider,
        extract_model=row.extract_model, extract_transport=row.extract_transport,
        toc_transport=row.toc_transport, output_language=row.output_language,
        content_provider=row.content_provider, content_model=row.content_model,
        content_transport=row.content_transport)


@router.get("/settings/launch-defaults", response_model=LaunchDefaultsOut)
async def get_launch_defaults(session: AsyncSession = Depends(get_session)) -> LaunchDefaultsOut:
    return _serialize(await launch_defaults_repo.get(session))


@router.put("/settings/launch-defaults", response_model=LaunchDefaultsOut)
async def put_launch_defaults(
    body: LaunchDefaultsUpdate,
    session: AsyncSession = Depends(get_session),
) -> LaunchDefaultsOut:
    fields = body.model_dump(exclude_unset=True)
    current = await launch_defaults_repo.get(session)
    # Validate the merged (provider, model) per role + each transport.
    merged = {**_serialize(current).model_dump(), **fields}
    # Finding #2: judge/extract provider+model MUST be concrete — they are the
    # terminal resolver; a null here bricks all Auto-role launches.
    for role in ("judge", "extract", "content"):
        prov = merged.get(f"{role}_provider")
        mdl = merged.get(f"{role}_model")
        if prov is None or mdl is None:
            raise HTTPException(
                422,
                "judge/extract provider+model must be concrete "
                "(the global default is the terminal resolver)",
            )
        if not is_valid(prov, mdl):
            raise HTTPException(422, f"{role}: off-manifest (provider, model) ({prov!r}, {mdl!r})")
    for role in ("judge", "extract"):
        t = merged.get(f"{role}_transport")
        if t is not None and (err := validate_role_transport(f"{role}_transport", t)) is not None:
            raise HTTPException(422, err)
    toc = merged.get("toc_transport")
    if toc is not None and toc not in ("cli", "api"):
        raise HTTPException(422, "toc_transport must be 'cli' or 'api'")
    # Finding #5: toc_transport=api requires an api-capable extract provider.
    if toc == "api" and not api_supported(merged.get("extract_provider") or ""):
        raise HTTPException(
            422,
            "toc_transport=api requires an api-capable extract_provider (claude/gemini)",
        )
    # output_language: the column is NOT NULL — the terminal value must be concrete.
    if err := validate_output_language(merged.get("output_language"), allow_none=False):
        raise HTTPException(422, err)
    ct = merged.get("content_transport")
    if ct is not None and ct not in ("cli", "api"):
        raise HTTPException(422, "content_transport must be 'cli' or 'api'")
    if ct == "api" and not api_supported(merged.get("content_provider") or ""):
        raise HTTPException(422, "content_transport=api requires an api-capable content_provider (claude/gemini)")
    out = _serialize(await launch_defaults_repo.update(session, fields))
    await session.commit()  # get_session yields without committing; persist the write
    return out
