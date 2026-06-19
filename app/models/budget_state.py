from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, Integer, String
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

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_budget_state_singleton"),
    )
