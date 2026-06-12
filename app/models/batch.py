from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
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
    notion_source: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("book_id", "transport", name="uq_batches_book_id_transport"),
    )
