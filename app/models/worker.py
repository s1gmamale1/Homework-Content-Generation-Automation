from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WorkerNode(Base):
    """One row per worker process in the fleet. Keyed by the worker's
    `hostname:pid` id (same value used for `homework_jobs.claimed_by`). The
    worker upserts `last_heartbeat`; liveness ('online') is derived from how
    fresh that timestamp is, not stored."""

    __tablename__ = "workers"

    pc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="online"
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
