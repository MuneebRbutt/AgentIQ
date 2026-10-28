"""The Follow-Up agent's decisions and the profile's follow-up settings (FR-07-01…04)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.followup.agent import (
    BookingFacts,
    ConversationFacts,
    FollowUpAgent,
    describe_when,
    pick_recipient,
)
from app.profiles.examples import load_example
from app.profiles.schema import BusinessProfileConfig, Domain, FollowUpRules

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
# Wed 21 Oct, 7 PM in Lahore (UTC+5).
STARTS = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)

agent = FollowUpAgent()


def _config(domain: Domain = Domain.RESTAURANT, **follow_up: Any) -> BusinessProfileConfig:
    config = load_example(domain)
    return config.model_copy(update={"follow_up": config.follow_up.model_copy(update=follow_up)})


BOOKING = BookingFacts(
    reference="K7M2Q9",
    status="confirmed",
    service_id="dine-in",
    party_size=4,
    starts_at=STARTS,
    ends_at=STARTS + timedelta(hours=2),
    customer_name="Ali Raza",
    customer_phone="+92 300 1234567",
    customer_email="ali@example.com",
)
CONVERSATION = ConversationFacts(
    status="closed",
    ended_at=NOW,
    customer_messages=2,
    customer_name="Ali Raza",
    customer_phone="+92 300 1234567",
    customer_email=None,
)


def _booking(**changes: Any) -> BookingFacts:
    return replace(BOOKING, **changes)


def _conversation(**changes: Any) -> ConversationFacts:
    return replace(CONVERSATION, **changes)


def test_tc07_a_new_booking_gets_a_confirmation_now_and_a_reminder_a_day_before() -> None:
    plan = agent.for_booking("created", _booking(), _config(), NOW)

    confirmation, reminder = plan.messages
    assert confirmation.kind == "confirmation" and confirmation.due_at == NOW
    assert (confirmation.channel, confirmation.recipient) == ("sms", "+92 300 1234567")
    assert confirmation.body == (
        "Spice Garden: your booking K7M2Q9 is confirmed for Table reservation, "
        "Wed 21 Oct at 7:00 PM, party of 4. Call +92 42 3570 2200 to change it."
    )
    assert reminder.kind == "reminder" and reminder.due_at == STARTS - timedelta(hours=24)
    assert "Reminder from Spice Garden" in reminder.body and "K7M2Q9" in reminder.body
    assert plan.cancel_pending is False and plan.skipped == []


def test_a_changed_booking_replaces_what_was_planned() -> None:
    plan = agent.for_booking("modified", _booking(party_size=6), _config(), NOW)
    assert plan.cancel_pending is True
    assert [m.kind for m in plan.messages] == ["confirmation", "reminder"]
    assert plan.messages[0].body.startswith("Spice Garden: your booking K7M2Q9 has been changed")
    assert "party of 6" in plan.messages[0].body


def test_a_cancelled_booking_only_withdraws_pending_messages() -> None:
    plan = agent.for_booking("cancelled", _booking(status="cancelled"), _config(), NOW)
    assert plan.messages == [] and plan.cancel_pending is True
    # A created event read after the booking was cancelled plans nothing either.
    late = agent.for_booking("created", _booking(status="cancelled"), _config(), NOW)
    assert late.messages == []


def test_reminders_follow_the_profile_rules() -> None:
    plan = agent.for_booking("created", _booking(), _config(reminder_hours_before=[48, 2]), NOW)
    due = [m.due_at for m in plan.messages if m.kind == "reminder"]
    assert due == [STARTS - timedelta(hours=48), STARTS - timedelta(hours=2)]


def test_no_reminder_when_the_booking_is_too_soon() -> None:
    soon = NOW + timedelta(hours=5)
    plan = agent.for_booking("created", _booking(starts_at=soon), _config(), NOW)
    assert [m.kind for m in plan.messages] == ["confirmation"]
    assert plan.skipped == ["reminder 24h: booking is sooner"]


def test_confirmations_can_be_turned_off() -> None:
    plan = agent.for_booking("created", _booking(), _config(confirmation=False), NOW)
    assert [m.kind for m in plan.messages] == ["reminder"]
    assert "confirmations off" in plan.skipped


def test_the_channel_falls_back_to_the_contact_the_customer_gave() -> None:
    email_first = _config(channel="email")
    plan = agent.for_booking("created", _booking(), email_first, NOW)
    assert (plan.messages[0].channel, plan.messages[0].recipient) == ("email", "ali@example.com")
    no_email = agent.for_booking("created", _booking(customer_email=None), email_first, NOW)
    assert no_email.messages[0].channel == "sms"
    nobody = agent.for_booking(
        "created", _booking(customer_phone=None, customer_email=None), _config(), NOW
    )
    assert nobody.messages == [] and nobody.skipped == ["no contact"]


def test_pick_recipient() -> None:
    rules = FollowUpRules()
    assert pick_recipient(rules, "+92 300", "a@b.co") == ("sms", "+92 300")
    assert pick_recipient(rules, None, "a@b.co") == ("email", "a@b.co")
    assert pick_recipient(rules, None, None) is None


def test_overnight_bookings_say_from_and_to() -> None:
    hotel = load_example(Domain.HOTEL)
    stay = _booking(
        service_id=hotel.services[0].id,
        starts_at=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
        ends_at=datetime(2026, 10, 23, 7, 0, tzinfo=UTC),
    )
    assert describe_when(stay, hotel) == "from Wed 21 Oct to Fri 23 Oct"
    plan = agent.for_booking("created", stay, hotel, NOW)
    assert plan.messages[0].channel == "email"  # the hotel prefers email
    assert "guest(s)" in plan.messages[0].body


def test_morning_times_read_naturally() -> None:
    morning = _booking(starts_at=datetime(2026, 10, 21, 4, 5, tzinfo=UTC))  # 9:05 AM local
    assert describe_when(morning, _config()) == "Wed 21 Oct at 9:05 AM"


# --- Feedback (FR-07-04) ----------------------------------------------------------------


def test_tc07_feedback_is_requested_after_the_conversation_ends() -> None:
    plan = agent.for_conversation(_conversation(), _config(feedback_delay_minutes=60))
    [message] = plan.messages
    assert message.kind == "feedback" and message.due_at == NOW + timedelta(minutes=60)
    assert message.body.startswith("Thank you for contacting Spice Garden. How did we do?")


@pytest.mark.parametrize(
    ("conversation", "config", "reason"),
    [
        (_conversation(), _config(feedback=False), "feedback off"),
        (_conversation(status="handed_off"), _config(), "handed to a person"),
        (_conversation(customer_messages=0), _config(), "customer said nothing"),
        (_conversation(customer_phone=None), _config(), "no contact"),
    ],
)
def test_no_feedback_request_when_it_does_not_fit(
    conversation: ConversationFacts, config: BusinessProfileConfig, reason: str
) -> None:
    plan = agent.for_conversation(conversation, config)
    assert plan.messages == [] and plan.skipped == [reason]


# --- Profile settings -------------------------------------------------------------------


def test_follow_up_defaults_keep_older_profiles_valid() -> None:
    raw = load_example(Domain.RESTAURANT).model_dump(mode="json")
    del raw["follow_up"]
    config = BusinessProfileConfig.model_validate(raw)
    assert config.follow_up.reminder_hours_before == [24]
    assert config.follow_up.confirmation is True and config.follow_up.feedback is False


def test_unknown_or_broken_placeholders_are_refused() -> None:
    with pytest.raises(ValidationError, match=r"unknown placeholder\(s\) \{price\}"):
        FollowUpRules(confirmation_message="Total {price}")
    with pytest.raises(ValidationError, match="broken placeholder"):
        FollowUpRules(reminder_message="See you {when")
    with pytest.raises(ValidationError, match=r"\{reference\}"):
        FollowUpRules(feedback_message="Rate booking {reference}")  # not known there


def test_reminder_rules_are_distinct_and_latest_first() -> None:
    assert FollowUpRules(reminder_hours_before=[2, 24]).reminder_hours_before == [24, 2]
    with pytest.raises(ValidationError, match="different number of hours"):
        FollowUpRules(reminder_hours_before=[24, 24])
    with pytest.raises(ValidationError):
        FollowUpRules(reminder_hours_before=[1, 2, 3, 4])
    with pytest.raises(ValidationError):
        FollowUpRules(reminder_hours_before=[0])
