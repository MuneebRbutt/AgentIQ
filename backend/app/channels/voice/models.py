"""Phone numbers and calls (FR-02-01, FR-02-06; analysis M-5; ADR-0010).

A `PhoneNumber` routes calls to one business profile: the tenant of a call comes from the
number that was dialled, never from anything said on the call (NFR03-02). A `VoiceCall`
links Twilio's call to the conversation it carries and keeps each turn's per-stage timings
for the latency report (FR-02-06, NFR01-01).
"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class PhoneNumber(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "phone_numbers"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE"), index=True
    )
    # E.164 ("+14155550123"), as Twilio sends it in `To`. One business per number.
    number: Mapped[str] = mapped_column(String(20), unique=True)


class VoiceCall(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    __tablename__ = "voice_calls"
    __table_args__ = (
        Index("ix_voice_calls_profile_started", "profile_id", "started_at"),
        CheckConstraint("status IN ('in_progress', 'ended')", name="known_status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interactions.id", ondelete="CASCADE"), index=True
    )
    call_sid: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20))
    # Twilio's final status: completed, busy, no-answer, failed, canceled.
    end_reason: Mapped[str | None] = mapped_column(String(20))
    # Prompts in a row that got no answer; reset when the caller speaks.
    silences: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # One entry per answered turn: {"stt_ms", "orchestrator_ms", "tts_ms", "server_ms",
    # "stt", "tts"} (FR-02-06).
    timings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    started_at: Mapped[Timestamp]
    ended_at: Mapped[Timestamp | None]
