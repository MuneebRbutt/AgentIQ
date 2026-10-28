"""The business-profile schema (FR-08-04, FR-08-05; ADR-0002).

A profile is the single source of a business's domain behaviour (NFR05-02, FR-08-07):
what it offers, when it is open, what can be booked and how, its policies, and when to
hand a customer to a person. Agents read these settings; they never branch on `domain`.
`domain` only picks the starter template and labels the profile.

Bookable capacity is generic (analysis D-3): a profile lists *resources* (a room type, a
table size, a practitioner), each with a number of identical units and a capacity per
unit. `booking_rules.mode` says whether bookings are time slots (restaurants, clinics) or
overnight stays (hotels).
"""

from collections import Counter
from collections.abc import Sequence
from datetime import time
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from string import Formatter
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

Slug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$", min_length=1, max_length=50),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+?[0-9 ()-]{6,24}$")]
Email = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
]


class Domain(StrEnum):
    HOTEL = "hotel"
    RESTAURANT = "restaurant"
    CLINIC = "clinic"


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class BookingMode(StrEnum):
    TIME_SLOT = "time_slot"  # a start time and a duration
    OVERNIGHT = "overnight"  # a check-in date and a number of nights


CustomerDetail = Literal["name", "phone", "email"]
_DEFAULT_DETAILS: list[CustomerDetail] = ["name", "phone"]


class _Section(BaseModel):
    # Unknown keys are mistakes (a typo would otherwise be silently ignored).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Contact(_Section):
    phone: Phone | None = None
    email: Email | None = None
    address: Annotated[str, StringConstraints(max_length=500)] | None = None
    website: Annotated[str, StringConstraints(max_length=300)] | None = None


class OpeningInterval(_Section):
    """`closes` earlier than `opens` means the interval runs past midnight."""

    opens: time
    closes: time

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.opens == self.closes:
            raise ValueError("opening and closing times must differ")
        return self

    @property
    def crosses_midnight(self) -> bool:
        return self.closes < self.opens


class Service(_Section):
    id: Slug
    name: ShortText
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None
    # Time-slot bookings for this service last this long (overrides the profile default).
    duration_minutes: Annotated[int, Field(ge=5, le=24 * 60)] | None = None
    price: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None


class Resource(_Section):
    """A kind of bookable unit, e.g. "Deluxe room" x 10 (2 guests each) or "Dr. Ali" x 1."""

    id: Slug
    name: ShortText
    kind: ShortText  # what the business calls it: "room type", "table", "doctor"
    quantity: Annotated[int, Field(ge=1, le=1000)]
    capacity: Annotated[int, Field(ge=1, le=1000)]  # people per unit
    # Services this resource can be booked for; empty means all of them.
    service_ids: list[Slug] = Field(default_factory=list)
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None


class CustomField(_Section):
    """Extra detail to collect with a booking, e.g. a clinic's "reason for visit"."""

    key: Slug
    label: ShortText
    required: bool = True


class BookingRules(_Section):
    mode: BookingMode

    # time_slot mode
    slot_minutes: Annotated[int, Field(ge=5, le=240)] | None = None
    default_duration_minutes: Annotated[int, Field(ge=5, le=24 * 60)] | None = None

    # overnight mode
    check_in_time: time | None = None
    check_out_time: time | None = None
    max_nights: Annotated[int, Field(ge=1, le=365)] | None = None

    min_party_size: Annotated[int, Field(ge=1, le=1000)] = 1
    max_party_size: Annotated[int, Field(ge=1, le=1000)]
    min_notice_minutes: Annotated[int, Field(ge=0, le=30 * 24 * 60)] = 0
    max_advance_days: Annotated[int, Field(ge=1, le=730)] = 90
    # Customers may change or cancel until this many hours before the booking (FR-04).
    cancellation_notice_hours: Annotated[int, Field(ge=0, le=720)] = 24

    required_details: list[CustomerDetail] = Field(default_factory=lambda: list(_DEFAULT_DETAILS))
    custom_fields: list[CustomField] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.mode is BookingMode.TIME_SLOT:
            if self.slot_minutes is None:
                raise ValueError("slot_minutes is required for time_slot bookings")
            if self.check_in_time or self.check_out_time or self.max_nights:
                raise ValueError("check-in/check-out settings only apply to overnight bookings")
        else:
            if self.check_in_time is None or self.check_out_time is None:
                raise ValueError("check_in_time and check_out_time are required for overnight")
            if self.slot_minutes or self.default_duration_minutes:
                raise ValueError("slot and duration settings only apply to time_slot bookings")
        if self.min_party_size > self.max_party_size:
            raise ValueError("min_party_size can't be larger than max_party_size")
        _require_unique([field.key for field in self.custom_fields], "custom field key")
        _require_unique(self.required_details, "required detail")
        return self


class EscalationRules(_Section):
    """When to hand a conversation to a person (FR-06). The dashboard always flags it."""

    # Escalate when the sentiment model is at least this sure the customer is upset.
    negative_sentiment_threshold: Annotated[float, Field(ge=0, le=1)] = 0.7
    keywords: list[ShortText] = Field(default_factory=list)  # e.g. "manager", "refund"
    # Also escalate after this many upset messages in a row, even if none alone was enough.
    negative_turns_in_a_row: Annotated[int, Field(ge=2, le=10)] = 3
    max_clarification_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    notify_email: Email | None = None
    notify_phone: Phone | None = None
    handoff_message: LongText = (
        "I'm sorry about this. I've passed your request to our team and someone will "
        "contact you shortly."
    )


# Placeholders each follow-up message may use (filled by the Follow-Up agent, FR-07).
BOOKING_PLACEHOLDERS = frozenset(
    {"business", "name", "reference", "service", "when", "party_size", "business_phone"}
)
FEEDBACK_PLACEHOLDERS = frozenset({"business", "name", "business_phone"})


def _check_placeholders(template: str, allowed: frozenset[str]) -> str:
    try:
        used = {field for _, field, _, _ in Formatter().parse(template) if field is not None}
    except ValueError as exc:  # an unmatched "{" or "}"
        raise ValueError(f"broken placeholder in message: {exc}") from exc
    unknown = sorted(used - allowed)
    if unknown:
        raise ValueError(
            "unknown placeholder(s) "
            + ", ".join("{" + name + "}" for name in unknown)
            + "; use "
            + ", ".join("{" + name + "}" for name in sorted(allowed))
        )
    return template


class FollowUpRules(_Section):
    """Messages sent after a conversation or booking, without anyone pressing a button
    (FR-07, UC-07; ADR-0007). Every message has a default, so a profile can leave this out."""

    confirmation: bool = True  # FR-07-02: on every booking created or changed
    # FR-07-03: one reminder per entry, this many hours before the booking starts.
    reminder_hours_before: Annotated[
        list[Annotated[int, Field(ge=1, le=336)]], Field(max_length=3)
    ] = Field(default_factory=lambda: [24])
    # FR-07-04: ask how it went, this long after a conversation ends.
    feedback: bool = False
    feedback_delay_minutes: Annotated[int, Field(ge=0, le=7 * 24 * 60)] = 60
    # How to reach the customer; the other channel is used when this contact is missing.
    channel: Literal["sms", "email"] = "sms"

    confirmation_message: LongText = (
        "{business}: your booking {reference} is confirmed for {service}, {when}, "
        "party of {party_size}. Call {business_phone} to change it."
    )
    update_message: LongText = (
        "{business}: your booking {reference} has been changed. It is now {service}, "
        "{when}, party of {party_size}."
    )
    reminder_message: LongText = (
        "Reminder from {business}: {service}, {when}, party of {party_size} "
        "(booking {reference}). Call {business_phone} if your plans change."
    )
    feedback_message: LongText = (
        "Thank you for contacting {business}. How did we do? Reply with a score from 1 "
        "to 5 and any comments."
    )

    @field_validator("reminder_hours_before")
    @classmethod
    def _distinct_reminders(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("each reminder needs a different number of hours")
        return sorted(value, reverse=True)

    @field_validator("confirmation_message", "update_message", "reminder_message")
    @classmethod
    def _booking_placeholders(cls, value: str) -> str:
        return _check_placeholders(value, BOOKING_PLACEHOLDERS)

    @field_validator("feedback_message")
    @classmethod
    def _feedback_placeholders(cls, value: str) -> str:
        return _check_placeholders(value, FEEDBACK_PLACEHOLDERS)


class Policy(_Section):
    title: ShortText
    text: LongText


class Faq(_Section):
    question: ShortText
    answer: LongText


class Assistant(_Section):
    """How the agent introduces itself and sounds (analysis AS-11)."""

    greeting: LongText
    tone: ShortText = "warm and professional"
    extra_instructions: Annotated[str, StringConstraints(max_length=2000)] | None = None


class BusinessProfileConfig(_Section):
    """Everything an operator configures for one business line (FR-08-04).

    Mandatory (FR-08-05): name, domain, timezone, opening hours, at least one service,
    at least one resource, booking rules and the assistant greeting.
    """

    name: ShortText
    domain: Domain
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    timezone: str  # IANA name, e.g. "Asia/Karachi" (analysis AS-12: one per profile)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "PKR"
    contact: Contact = Field(default_factory=Contact)

    opening_hours: dict[Weekday, list[OpeningInterval]]  # a missing day means closed
    services: Annotated[list[Service], Field(min_length=1, max_length=200)]
    resources: Annotated[list[Resource], Field(min_length=1, max_length=200)]
    booking_rules: BookingRules

    policies: list[Policy] = Field(default_factory=list, max_length=100)
    faqs: list[Faq] = Field(default_factory=list, max_length=200)
    escalation: EscalationRules = Field(default_factory=EscalationRules)
    follow_up: FollowUpRules = Field(default_factory=FollowUpRules)
    assistant: Assistant

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}; use a name like Asia/Karachi") from exc
        return value

    @field_validator("opening_hours")
    @classmethod
    def _sensible_hours(
        cls, value: dict[Weekday, list[OpeningInterval]]
    ) -> dict[Weekday, list[OpeningInterval]]:
        if not any(value.values()):
            raise ValueError("the business must be open at least one day a week")
        for day, intervals in value.items():
            ordered = sorted(intervals, key=lambda interval: interval.opens)
            for earlier, later in pairwise(ordered):
                if earlier.crosses_midnight or later.opens < earlier.closes:
                    raise ValueError(f"opening hours overlap on {day.value}")
            value[day] = ordered
        return value

    @field_validator("services")
    @classmethod
    def _unique_services(cls, value: list[Service]) -> list[Service]:
        _require_unique([service.id for service in value], "service id")
        return value

    @field_validator("resources")
    @classmethod
    def _valid_resources(cls, value: list[Resource], info: ValidationInfo) -> list[Resource]:
        _require_unique([resource.id for resource in value], "resource id")
        # Only cross-check when services themselves were valid; otherwise that error shows.
        services = info.data.get("services")
        if services is not None:
            known = {service.id for service in services}
            for resource in value:
                unknown = sorted(set(resource.service_ids) - known)
                if unknown:
                    raise ValueError(
                        f"resource {resource.id!r} refers to unknown service(s): "
                        + ", ".join(unknown)
                    )
        return value

    @model_validator(mode="after")
    def _durations_known(self) -> Self:
        rules = self.booking_rules
        if rules.mode is BookingMode.TIME_SLOT and rules.default_duration_minutes is None:
            missing = [s.id for s in self.services if s.duration_minutes is None]
            if missing:
                raise ValueError(
                    "set booking_rules.default_duration_minutes or a duration for service(s): "
                    + ", ".join(missing)
                )
        return self


def _require_unique(values: Sequence[str], what: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {what}: {', '.join(duplicates)}")
