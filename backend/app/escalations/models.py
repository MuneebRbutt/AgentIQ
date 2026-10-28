"""Escalations: conversations handed to a person (FR-06-04, FR-08-03; analysis Q-6).

No human agents are part of the system, so "escalating" means: record it here, show it on
the dashboard, notify the business's contact, and tell the customer someone will call back.
"""

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class Escalation(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "escalations"
    __table_args__ = (
        Index("ix_escalations_profile_status_created", "profile_id", "status", "created_at"),
        CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name="known_status"),
        CheckConstraint(
            "notification IN ('none', 'logged', 'sent', 'failed')", name="known_notification"
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    turn_seq: Mapped[int] = mapped_column(Integer)  # the customer message that triggered it
    # negative_sentiment, keyword, repeated_negative (FR-06-04) or unclear (FR-03-05).
    reasons: Mapped[list[str]] = mapped_column(JSONB)
    customer_message: Mapped[str] = mapped_column(Text)
    customer_phone: Mapped[str | None] = mapped_column(String(32))  # to call back
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    # Whether the business's contact was told: none (no contact set), logged (no SMS or
    # email provider configured), sent or failed (a retry is queued, ADR-0007).
    notification: Mapped[str] = mapped_column(String(20))
    notified_at: Mapped[Timestamp | None]
    note: Mapped[str | None] = mapped_column(Text)  # the operator's, when handling it
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL")
    )
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())
