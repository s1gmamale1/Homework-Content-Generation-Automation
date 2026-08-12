from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPK


class Batch(Base, UUIDPK, Timestamps):
    """One row per textbook generation batch. UNIQUE(book_id, transport,
    output_language, kind) -> one batch per (book, transport, output_language,
    kind); a different-transport, different-output-language, or different-kind
    re-launch forks a new batch, same tuple reuses it. That makes find-or-create
    race-safe (ON CONFLICT) and adoption unambiguous. No status counters: the
    rollup is computed on read (DISTINCT ON over the batch's jobs). provider/model
    are the launch-default label only - per-job provider/model are authoritative.
    `kind` ("homework" default vs "teacher_material") scopes which deliverable type
    the batch belongs to - every read path that resolves the latest job/batch for a
    section or book is kind-scoped so the two deliverable types stay isolated."""

    __tablename__ = "batches"

    book_id: Mapped[UUID] = mapped_column(ForeignKey("books.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Generation transport: "cli" (subprocess CLI, default) vs "api" (pay-per-token SDK).
    transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="cli")
    # Output language for generated content: "uz" (default), "en", or "ru".
    output_language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="uz")
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
    solver_transport: Mapped[str] = mapped_column(String(16), nullable=False, server_default="inherit")
    solver_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    solver_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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
    # Deliverable discriminator: "homework" (default, student packet, the
    # 11-phase flow) vs "teacher_material" (single structured teacher-deck
    # phase). Part of the batch's widened unique key below; scopes every
    # section/book/batch read path so the two deliverable types stay isolated.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default="homework")

    __table_args__ = (
        UniqueConstraint("book_id", "transport", "output_language", "kind",
                         name="uq_batches_book_id_transport_output_language_kind"),
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
            "solver_transport IN ('cli','api','inherit')",
            name="ck_batches_solver_transport",
        ),
        CheckConstraint(
            "session_limit_strategy IN ('pause','switch','inherit')",
            name="ck_batches_session_limit_strategy",
        ),
        CheckConstraint(
            "output_language IN ('uz','en','ru')",
            name="ck_batches_output_language",
        ),
    )
