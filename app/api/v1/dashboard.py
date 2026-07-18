"""Read-only aggregate feeding the /dashboard page.

Deliberately NOT built on `GET /jobs/batches`: that route only sees launched
books (so "ready, not started" is invisible) and is 3N+1. This is three
set-based queries + one pure classify pass per book.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import subject_coverage as cov_repo
from app.schemas.dashboard import CoverageOut
from app.services.subject_coverage import build_coverage, entry_to_dict

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    output_language: Literal["uz", "en", "ru"] = Query("uz"),
    session: AsyncSession = Depends(get_session),
) -> CoverageOut:
    books = await cov_repo.all_books(session)
    toc_by_book = await cov_repo.toc_rows_by_book(session, [b.id for b in books])
    job_status = await cov_repo.job_status_by_book(session, output_language)
    batches = await cov_repo.batch_by_book(session, output_language)
    entries = build_coverage(books, toc_by_book, job_status, batches)
    return CoverageOut(
        output_language=output_language,
        entries=[entry_to_dict(e) for e in entries],
    )
