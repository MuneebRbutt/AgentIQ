"""Customers, bookings and booking history (FR-04-01, FR-04-06, FR-09-01/02; ADR-0003)."""

import uuid
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class Customer(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """Someone who books with a business, identified by phone number (analysis AS-5)."""

    __tablename__ = "customers"
    __table_args__ = (Index("uq_customers_tenant_phone", "tenant_id", "phone_key", unique=True),)

    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    # Last 10 digits, so "+92 300 1234567" and "0300-1234567" are the same person.
    phone_key: Mapped[str] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(320))


class Booking(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """One reservation of one unit of a profile's resource for a period."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index("uq_bookings_tenant_reference", "tenant_id", "reference", unique=True),
        # Availability checks look up a resource's bookings around a time.
        Index("ix_bookings_availability", "profile_id", "resource_id", "starts_at"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("party_size > 0", name="positive_party_size"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="known_status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), index=True
    )
    reference: Mapped[str] = mapped_column(String(12))  # what the customer quotes
    service_id: Mapped[str] = mapped_column(String(50))  # ids from the profile
    resource_id: Mapped[str] = mapped_column(String(50))
    party_size: Mapped[int]
    starts_at: Mapped[Timestamp]
    ends_at: Mapped[Timestamp]
    status: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the profile's custom fields
    notes: Mapped[str | None] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(20))  # "agent" or "dashboard"
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())
    cancelled_at: Mapped[Timestamp | None]


class BookingEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Every create, change and cancel, kept for later retrieval (FR-04-06).

    The Follow-Up worker consumes these as its trigger events (analysis C-8, ADR-0007).
    """

    __tablename__ = "booking_events"
    __table_args__ = (
        Index(
            "ix_booking_events_to_follow_up", "seq", postgresql_where=text("followed_up_at IS NULL")
        ),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), index=True
    )
    # Orders events reliably even when two share a timestamp.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # created | modified | cancelled
    actor: Mapped[str] = mapped_column(String(80))  # "customer" or "operator:<id>"
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # When the Follow-Up worker handled this event (ADR-0007); null = still to do.
    followed_up_at: Mapped[Timestamp | None]
