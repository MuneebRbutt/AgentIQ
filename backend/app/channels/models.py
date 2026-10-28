"""Records shared by the text-message and email channels (FR-02-02/03; ADR-0011)."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class InboundMessage(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """One message received, kept to answer it once and to count messages per sender."""

    __tablename__ = "inbound_messages"
    __table_args__ = (
        Index("uq_inbound_messages_channel_external", "channel", "external_id", unique=True),
        Index("ix_inbound_messages_sender", "profile_id", "sender", "created_at"),
        CheckConstraint("channel IN ('sms', 'email')", name="known_channel"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interactions.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(10))
    # Twilio's MessageSid, or the email's Message-ID.
    external_id: Mapped[str] = mapped_column(String(255))
    # E.164 phone number or lower-case email address.
    sender: Mapped[str] = mapped_column(String(320))
