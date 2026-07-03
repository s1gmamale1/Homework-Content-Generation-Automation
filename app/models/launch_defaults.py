from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LaunchDefaults(Base):
    """Singleton (exactly one row, id=1) holding the UI-managed global launch
    defaults for the judge/extract roles + upload-time TOC transport. The
    launch endpoints resolve each role (explicit pick -> else this row) and
    stamp the concrete value onto every job. CHECK(id=1) enforces the singleton.
    Columns are nullable so a partial PUT can touch one field; the migration
    seeds the row with concrete values so reads always see a populated default.
    """

    __tablename__ = "launch_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    extract_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    toc_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Default output language for generated content: "uz", "en", or "ru".
    # NOT NULL with server_default "uz" — matches the column definition on homework_jobs/batches.
    output_language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="uz")
    content_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    content_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    content_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    solver_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    solver_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    solver_transport: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_launch_defaults_singleton"),
    )
