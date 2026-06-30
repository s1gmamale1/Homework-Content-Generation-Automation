from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class Book(Base, UUIDPK, Timestamps):
    __tablename__ = "books"

    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gemini_file_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    gemini_file_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Per-book Gemini context cache (saves ~75% on input tokens for the
    # extract phase across multiple jobs against the same book).
    gemini_cache_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    gemini_cache_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Language of the source textbook: "uz" (Uzbek, default), "ru" (Russian), "en" (English).
    source_language: Mapped[str] = mapped_column(String(8), nullable=False, server_default="uz")
    # Post-TOC-extract vision validator result: verified | mismatch | skipped | NULL (not yet run).
    toc_validation: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    toc_validation_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    toc_entries: Mapped[list["TOCEntry"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="TOCEntry.order_index",
    )

    __table_args__ = (
        Index("ix_books_content_sha256", "content_sha256"),
        CheckConstraint(
            "source_language IN ('uz','ru','en')",
            name="ck_books_source_language",
        ),
        CheckConstraint(
            "toc_validation IS NULL OR toc_validation IN ('verified','mismatch','skipped')",
            name="ck_books_toc_validation",
        ),
    )


from app.models.toc_entry import TOCEntry  # noqa: E402
