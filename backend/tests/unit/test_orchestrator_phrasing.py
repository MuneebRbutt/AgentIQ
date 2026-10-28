"""Template replies used without a model, and the prompts given to one (NFR04-01)."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app import cli
from app.agents.contracts import AgentResult, AgentStatus
from app.knowledge.profile_facts import profile_facts
from app.orchestrator.phrasing import DECLINED, NO_ANSWER, SKIPPED, field_words, template_reply
from app.orchestrator.planning import NOTE_DECLINED
from app.orchestrator.prompting import reply_messages, understand_messages
from app.profiles.examples import load_example
from app.profiles.schema import Domain

RESTAURANT = load_example(Domain.RESTAURANT)
HOTEL = load_example(Domain.HOTEL)


def _r(status: AgentStatus, message: str = "m", **extra: object) -> AgentResult:
    return AgentResult(agent="booking", status=status, message=message, **extra)


def test_template_reply_covers_every_status() -> None:
    results: list[tuple[str, AgentResult | None]] = [
        ("a", _r(AgentStatus.COMPLETED, "Booked.")),
        (
            "b",
            _r(AgentStatus.NEEDS_INFO, missing=["customer_name", "party_size", "details.occasion"]),
        ),
        ("c", _r(AgentStatus.NEEDS_CONFIRMATION, "Please confirm: Table for 2.")),
        ("d", _r(AgentStatus.NO_ANSWER)),
        ("e", None),
    ]
    assert template_reply(RESTAURANT, results, [NOTE_DECLINED]) == " ".join(
        [
            "Booked.",
            "Could you tell me your name, the number of people and the special occasion?",
            "Please confirm: Table for 2. Shall I go ahead?",
            NO_ANSWER,
            SKIPPED,
            DECLINED,
        ]
    )


def test_template_reply_with_nothing_to_say_offers_help() -> None:
    assert template_reply(RESTAURANT, [], []) == "How can I help you with Spice Garden today?"


def test_field_words_fall_back_to_the_key() -> None:
    assert field_words(RESTAURANT, "time") == "the time"
    assert field_words(RESTAURANT, "details.unknown") == "the unknown"
    assert field_words(RESTAURANT, "some_field") == "some field"


def test_understand_prompt_describes_the_business_and_the_turn() -> None:
    now = datetime(2026, 10, 19, 15, 0, tzinfo=ZoneInfo("Asia/Karachi"))
    system, user = understand_messages(
        RESTAURANT, now, [{"role": "customer", "text": "hi"}], {"party_size": 2}, True, "yes"
    )
    assert '"dine-in" (Table reservation)' in system["content"]
    assert '"occasion" (Special occasion)' in system["content"]
    assert "Today is 2026-10-19 (Monday)" in system["content"]
    assert "(not used here)" in system["content"]  # nights, for a restaurant
    context = json.loads(user["content"])
    assert context == {
        "conversation": [{"role": "customer", "text": "hi"}],
        "booking_in_progress": {"party_size": 2},
        "awaiting_confirmation": True,
        "message": "yes",
    }
    hotel_system, _ = understand_messages(HOTEL, now, [], None, False, "hi")
    assert "the check-in date" in hotel_system["content"]


def test_reply_prompt_carries_tone_results_and_skips() -> None:
    results: list[tuple[str, AgentResult | None]] = [
        ("a", _r(AgentStatus.UNAVAILABLE, "Full.", alternatives=[{"description": "20:00"}])),
        ("b", None),
    ]
    system, user = reply_messages(RESTAURANT, "book", results, ["declined_confirmation"])
    assert "friendly and upbeat" in system["content"]
    payload = json.loads(user["content"])
    assert payload["results"] == [
        {
            "agent": "booking",
            "status": "unavailable",
            "message": "Full.",
            "alternatives": ["20:00"],
        },
        {"status": "skipped"},
    ]
    assert payload["notes"] == ["declined_confirmation"]


def test_profile_facts_read_naturally() -> None:
    facts = profile_facts(RESTAURANT)
    assert facts[0].startswith("Opening hours: Monday to Thursday 12:00 to 15:30 and 19:00")
    assert "Contact Spice Garden: phone +92 42 3570 2200" in facts[2]
    assert "Do you serve halal food? Yes, all our food is halal." in facts
    hotel = profile_facts(HOTEL)
    assert hotel[0] == "Opening hours: Monday to Sunday open 24 hours."
    assert any("Price: PKR" in fact for fact in hotel)


def test_chat_command_parses() -> None:
    args = cli.build_parser().parse_args(
        ["chat", "--profile-id", "8b0d3c46-7f1e-4a43-9d8c-3f7a2b1c0d9e", "--phone", "0300"]
    )
    assert args.phone == "0300"
