from typing import Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPK


class TOCEntry(Base, UUIDPK):
    __tablename__ = "toc_entries"

    book_id: Mapped[UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    chapter_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    section_number: Mapped[str] = mapped_column(String(32), nullable=False)
    section_title: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)

    book: Mapped["Book"] = relationship(back_populates="toc_entries")

    __table_args__ = (Index("ix_toc_entries_book_id_order", "book_id", "order_index"),)


from app.models.book import Book  # noqa: E402
