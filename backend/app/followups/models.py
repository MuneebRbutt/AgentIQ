"""Follow-up messages: queued, sent and retried (FR-07, UC-07; ADR-0007).

Each row is one message to one person at one time. Postgres is the queue (NFR04-02): the
worker takes due rows with `FOR UPDATE SKIP LOCKED`, so several workers never send the
same message, and nothing is lost if Redis or a worker restarts.
"""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class FollowUpTask(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "follow_up_tasks"
    __table_args__ = (
        # The worker's query: what is due now, oldest first.
        Index(
            "ix_follow_up_tasks_due",
            "due_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_follow_up_tasks_profile_created", "profile_id", "created_at"),
        CheckConstraint(
            "kind IN ('confirmation', 'reminder', 'feedback', 'escalation')", name="known_kind"
        ),
        CheckConstraint("channel IN ('sms', 'email')", name="known_channel"),
        CheckConstraint(
            "status IN ('pending', 'sent', 'logged', 'failed', 'cancelled')", name="known_status"
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    # confirmation / reminder / feedback go to the customer (FR-07-02/03/04); escalation is
    # the retry of a failed notice to the business (FR-06-04, UC-07 "retry once").
    kind: Mapped[str] = mapped_column(String(20))
    booking_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    escalation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("escalations.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(10))
    recipient: Mapped[str] = mapped_column(String(320))  # phone number or email address
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    due_at: Mapped[Timestamp]
    # pending → sent | logged (no SMS/email provider configured) | failed | cancelled
    status: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(300))  # never the provider's body
    sent_at: Mapped[Timestamp | None]
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())
