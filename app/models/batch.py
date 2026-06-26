from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPK


class Batch(Base, UUIDPK, Timestamps):
    """One row per textbook generation batch. UNIQUE(book_id, transport) -> one
    batch per (book, transport); a different-transport re-launch forks a new
    batch, same-transport reuses it. That makes find-or-create race-safe
    (ON CONFLICT) and adoption unambiguous. No status counters: the rollup is
    computed on read (DISTINCT ON over the batch's jobs). provider/model are the
    launch-default label only - per-job provider/model are authoritative."""

    __tablename__ = "batches"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Generation transport: "cli" (subprocess CLI, default) vs "api" (pay-per-token SDK).
    transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cli")
    # Per-role transport overrides: "cli" | "api" | "inherit" (follow the batch's transport).
    extract_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    judge_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    # Mirror of the launch's per-phase overrides + phase subset (provenance label).
    custom_prompts: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    selected_phases: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Per-role provider/model launch-default labels (mirror homework_jobs).
    extract_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extract_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    judge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    judge_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    notion_source: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Pause primitive (C5 fleet-ctrl-3 reuses this). NULL = not paused.
    paused_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    paused_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Session-limit strategy: what the worker does when a Claude session-limit hits.
    # "pause" = pause the batch and wait for the session to reset.
    # "switch" = switch to the failover provider and continue.
    # "inherit" = follow the env default (settings.session_limit_strategy).
    session_limit_strategy: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="inherit"
    )

    __table_args__ = (
        UniqueConstraint("book_id", "transport", name="uq_batches_book_id_transport"),
        CheckConstraint(
            "transport IN ('cli','api')",
            name="ck_batches_transport",
        ),
        CheckConstraint(
            "extract_transport IN ('cli','api','inherit')",
            name="ck_batches_extract_transport",
        ),
        CheckConstraint(
            "judge_transport IN ('cli','api','inherit')",
            name="ck_batches_judge_transport",
        ),
        CheckConstraint(
            "session_limit_strategy IN ('pause','switch','inherit')",
            name="ck_batches_session_limit_strategy",
        ),
    )
