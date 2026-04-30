from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPK


class Book(Base, UUIDPK, Timestamps):
    __tablename__ = "books"

    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gemini_file_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    gemini_file_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    toc_entries: Mapped[list["TOCEntry"]] = relationship(
        back_populates="book", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_books_content_sha256", "content_sha256"),)


from app.models.toc_entry import TOCEntry  # noqa: E402
