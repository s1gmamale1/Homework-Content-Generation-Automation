from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPK, _utcnow


class BookNotionSource(Base, UUIDPK):
    """Maps a Notion (page_id, block_id) pair — the ingest source a textbook
    was linked from — to the book it resolved to. Ids are normalized
    (hyphen-strip + lowercase, see repositories.notion_sources.normalize_notion_id)
    before storage/lookup: the Notion API returns hyphenated UUIDs, but a
    config-driven or manually-entered source may not (PR #96 gate class).

    UNIQUE(notion_page_id, notion_block_id): a source resolves to exactly one
    book at a time. A re-prepare after SHA-dedup re-points the row at the
    deduped book via ON CONFLICT ... DO UPDATE (see notion_sources.upsert_link),
    it never inserts a duplicate.
    """

    __tablename__ = "book_notion_sources"

    book_id: Mapped[UUID] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    notion_page_id: Mapped[str] = mapped_column(Text, nullable=False)
    notion_block_id: Mapped[str] = mapped_column(Text, nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "notion_page_id", "notion_block_id",
            name="uq_book_notion_sources_page_block",
        ),
    )
