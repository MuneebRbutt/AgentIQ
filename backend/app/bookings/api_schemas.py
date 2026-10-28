"""Request and response shapes of the booking API."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.bookings.availability import Check

Text200 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class CustomerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Text200
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)] | None = None


class BookingIn(BaseModel):
    """A booking made by an operator on a customer's behalf."""

    model_config = ConfigDict(extra="forbid")

    service_id: str
    party_size: int = Field(ge=1, le=1000)
    # Local time at the business ("2026-10-20T19:00"); for overnight stays the check-in date.
    start: datetime
    nights: int | None = Field(default=None, ge=1, le=365)
    customer: CustomerIn
    details: dict[str, Text200] = Field(default_factory=dict)
    notes: Annotated[str, StringConstraints(max_length=1000)] | None = None


class BookingPatch(BaseModel):
    """Fields to change; omitted fields keep their value (FR-04-04)."""

    model_config = ConfigDict(extra="forbid")

    service_id: str | None = None
    party_size: int | None = Field(default=None, ge=1, le=1000)
    start: datetime | None = None
    nights: int | None = Field(default=None, ge=1, le=365)
    details: dict[str, Text200] | None = None
    notes: Annotated[str, StringConstraints(max_length=1000)] | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str
    email: str | None


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    profile_id: uuid.UUID
    service_id: str
    resource_id: str
    party_size: int
    starts_at: datetime
    ends_at: datetime
    status: str
    details: dict[str, Any]
    notes: str | None
    source: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None
    customer: CustomerOut


class BookingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    actor: str
    changes: dict[str, Any]
    created_at: datetime


class SlotOut(BaseModel):
    resource_id: str
    starts_at: datetime
    ends_at: datetime


class AvailabilityOut(BaseModel):
    available: bool
    reason: str | None
    slot: SlotOut | None
    alternatives: list[SlotOut]


def slot_out(check: Check) -> SlotOut | None:
    if not check.available:
        return None
    period, resource_id = check.slot()
    return SlotOut(resource_id=resource_id, starts_at=period.starts_at, ends_at=period.ends_at)
