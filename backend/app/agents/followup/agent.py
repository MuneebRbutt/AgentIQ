"""The Follow-Up agent: decides which messages an outcome calls for (FR-07-01…04, UC-07).

It is event-driven (analysis C-8, ADR-0007), not a step of the conversation turn: the
worker hands it what happened (a booking event or an ended conversation) and stores the
messages it plans. Like the other agents it is pure: it reads the profile's `follow_up`
settings, never branches on the domain, and never touches the database.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.profiles.schema import BookingMode, BusinessProfileConfig, FollowUpRules

Kind = Literal["confirmation", "reminder", "feedback"]
Channel = Literal["sms", "email"]
EventKind = Literal["created", "modified", "cancelled"]


@dataclass(frozen=True, slots=True)
class BookingFacts:
    reference: str
    status: str  # confirmed | cancelled
    service_id: str
    party_size: int
    starts_at: datetime
    ends_at: datetime
    customer_name: str
    customer_phone: str | None
    customer_email: str | None


@dataclass(frozen=True, slots=True)
class ConversationFacts:
    status: str  # open | handed_off | closed
    ended_at: datetime
    customer_messages: int
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None


@dataclass(frozen=True, slots=True)
class PlannedMessage:
    kind: Kind
    channel: Channel
    recipient: str
    subject: str
    body: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class FollowUpPlan:
    """What to send, and whether earlier unsent messages for the booking are now stale."""

    messages: list[PlannedMessage] = field(default_factory=list)
    cancel_pending: bool = False
    # Why nothing (or less than everything) was planned, for the log (FR-07-01).
    skipped: list[str] = field(default_factory=list)


def pick_recipient(
    rules: FollowUpRules, phone: str | None, email: str | None
) -> tuple[Channel, str] | None:
    """The profile's channel when that contact is known, else the other one."""
    preferred: list[tuple[Channel, str | None]] = [("sms", phone), ("email", email)]
    if rules.channel == "email":
        preferred.reverse()
    for channel, contact in preferred:
        if contact:
            return channel, contact
    return None


class FollowUpAgent:
    name = "followup"

    def for_booking(
        self,
        event: EventKind,
        booking: BookingFacts,
        config: BusinessProfileConfig,
        now: datetime,
    ) -> FollowUpPlan:
        """A booking was created, changed or cancelled.

        Created or changed: a confirmation now (FR-07-02) and one reminder per configured
        rule (FR-07-03). A change or cancellation makes earlier unsent ones stale.
        """
        rules = config.follow_up
        if event == "cancelled" or booking.status != "confirmed":
            return FollowUpPlan(cancel_pending=True, skipped=["booking cancelled"])
        target = pick_recipient(rules, booking.customer_phone, booking.customer_email)
        if target is None:
            return FollowUpPlan(cancel_pending=event == "modified", skipped=["no contact"])
        channel, recipient = target
        values = _booking_values(booking, config)
        messages: list[PlannedMessage] = []
        skipped: list[str] = []

        if rules.confirmation:
            template = rules.confirmation_message if event == "created" else rules.update_message
            subject = "Booking confirmed" if event == "created" else "Booking changed"
            messages.append(
                PlannedMessage(
                    "confirmation",
                    channel,
                    recipient,
                    f"{config.name}: {subject.lower()}",
                    template.format_map(values),
                    now,
                )
            )
        else:
            skipped.append("confirmations off")

        for hours in rules.reminder_hours_before:
            due = booking.starts_at - timedelta(hours=hours)
            if due <= now:  # too late to be a reminder; the confirmation covers it
                skipped.append(f"reminder {hours}h: booking is sooner")
                continue
            messages.append(
                PlannedMessage(
                    "reminder",
                    channel,
                    recipient,
                    f"{config.name}: booking reminder",
                    rules.reminder_message.format_map(values),
                    due,
                )
            )
        return FollowUpPlan(messages, cancel_pending=event == "modified", skipped=skipped)

    def for_conversation(
        self, conversation: ConversationFacts, config: BusinessProfileConfig
    ) -> FollowUpPlan:
        """A conversation ended: ask for feedback when the profile collects it (FR-07-04).

        Not after a hand-off (a person is about to call), and not when the customer never
        said anything; an inquiry otherwise needs no follow-up (UC-07 alternative path).
        """
        rules = config.follow_up
        if not rules.feedback:
            return FollowUpPlan(skipped=["feedback off"])
        if conversation.status != "closed":
            return FollowUpPlan(skipped=["handed to a person"])
        if conversation.customer_messages == 0:
            return FollowUpPlan(skipped=["customer said nothing"])
        target = pick_recipient(rules, conversation.customer_phone, conversation.customer_email)
        if target is None:
            return FollowUpPlan(skipped=["no contact"])
        channel, recipient = target
        values = {
            "business": config.name,
            "name": conversation.customer_name or "there",
            "business_phone": config.contact.phone or "us",
        }
        due = conversation.ended_at + timedelta(minutes=rules.feedback_delay_minutes)
        message = PlannedMessage(
            "feedback",
            channel,
            recipient,
            f"{config.name}: how did we do?",
            rules.feedback_message.format_map(values),
            due,
        )
        return FollowUpPlan([message])


def describe_when(booking: BookingFacts, config: BusinessProfileConfig) -> str:
    """The booking's time at the business, e.g. "Wed 21 Oct at 7:00 PM"."""
    zone = ZoneInfo(config.timezone)
    start = booking.starts_at.astimezone(zone)
    if config.booking_rules.mode is BookingMode.OVERNIGHT:
        end = booking.ends_at.astimezone(zone)
        return f"from {_day(start)} to {_day(end)}"
    return f"{_day(start)} at {_clock(start)}"


def _day(moment: datetime) -> str:
    return f"{moment:%a} {moment.day} {moment:%b}"


def _clock(moment: datetime) -> str:
    return f"{moment.hour % 12 or 12}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


def _booking_values(booking: BookingFacts, config: BusinessProfileConfig) -> dict[str, str]:
    services = {service.id: service.name for service in config.services}
    return {
        "business": config.name,
        "name": booking.customer_name,
        "reference": booking.reference,
        "service": services.get(booking.service_id, booking.service_id),
        "when": describe_when(booking, config),
        "party_size": str(booking.party_size),
        "business_phone": config.contact.phone or "us",
    }
