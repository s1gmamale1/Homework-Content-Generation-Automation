from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, _utcnow


class SAKey(Base):
    """One uploaded GCP service-account key. The raw JSON (incl. private_key)
    lives on disk at storage.sa_key_path(id); only metadata is stored here."""

    __tablename__ = "sa_keys"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_email: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_concurrent_calls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class SAKeyAssignment(Base):
    """Which SA key a worker host should use. Keyed by bare hostname (stable
    across restarts, unlike workers.pc_id=hostname:pid). key_id NULL +
    scrub_requested_at set = an active 'clear this host's key' signal."""

    __tablename__ = "sa_key_assignments"

    hostname: Mapped[str] = mapped_column(Text, primary_key=True)
    key_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sa_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )
    scrub_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
