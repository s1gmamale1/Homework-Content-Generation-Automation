from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BudgetState(Base):
    """Singleton table (exactly one row, id=1) holding fleet-level pause state.

    When api_paused_at is not NULL, the fleet-daily gate is active and no
    api-spending jobs may be claimed by any worker. cli jobs are unaffected.

    The CHECK(id = 1) constraint enforces the singleton at the DB level.
    """

    __tablename__ = "budget_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_paused_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    api_paused_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Fleet worker version floor (fleet-worker-version-gate-1, mig 0046):
    # workers whose code_version is below this claim NOTHING. NULL = gate off.
    # Auto-stamped raise-only by main.lifespan; PUT /workers/version-floor is
    # the operator escape hatch (can lower/clear).
    min_worker_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_worker_version_stamped_by: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    min_worker_version_stamped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_budget_state_singleton"),
    )
