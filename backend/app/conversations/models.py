"""Conversations (interactions) and their turns (FR-09-05, NFR04-03; ADR-0005).

The interaction row holds the orchestrator's memory between turns (the booking being put
together, whether a read-back awaits confirmation, clarification attempts), so a
conversation continues after a backend restart (TC-09 step 3).
"""

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class Interaction(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interactions_profile_updated", "profile_id", "updated_at"),
        # The Follow-Up worker's query: ended conversations it hasn't looked at.
        Index(
            "ix_interactions_to_follow_up",
            "updated_at",
            postgresql_where=text("status <> 'open' AND followed_up_at IS NULL"),
        ),
        CheckConstraint("status IN ('open', 'handed_off', 'closed')", name="known_status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(String(20))  # text, sms, voice, email
    customer_phone: Mapped[str | None] = mapped_column(String(32))  # from caller ID / sender
    customer_email: Mapped[str | None] = mapped_column(String(320))  # an email's sender
    status: Mapped[str] = mapped_column(String(20))
    memory: Mapped[dict[str, Any]] = mapped_column(JSONB)
    turn_count: Mapped[int] = mapped_column(Integer)
    # An uncertain sentiment reading asks a person to look (UC-06 exception).
    needs_review: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # When the Follow-Up worker looked at the ended conversation (ADR-0007); null = not yet.
    followed_up_at: Mapped[Timestamp | None]
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())


class Turn(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """One message: the customer's words or the assistant's reply."""

    __tablename__ = "turns"
    __table_args__ = (
        Index("uq_turns_interaction_seq", "interaction_id", "seq", unique=True),
        CheckConstraint("role IN ('customer', 'assistant')", name="known_role"),
    )

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    # Assistant turns: understood subtasks, agent results and per-stage timings (FR-10-02).
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SentimentRecord(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """How one customer message sounded (FR-06-03/05), for escalation and analysis of how
    a conversation's sentiment developed."""

    __tablename__ = "sentiment_records"
    __table_args__ = (
        Index("uq_sentiment_records_interaction_seq", "interaction_id", "turn_seq", unique=True),
        CheckConstraint("label IN ('negative', 'neutral', 'positive')", name="known_label"),
    )

    interaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE")
    )
    turn_seq: Mapped[int] = mapped_column(Integer)  # the customer's turn
    label: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(200))
    low_confidence: Mapped[bool] = mapped_column(Boolean)
    complaint: Mapped[bool] = mapped_column(Boolean)
    reasons: Mapped[list[str]] = mapped_column(JSONB)  # why it escalated, if it did
