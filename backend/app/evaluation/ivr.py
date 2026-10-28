"""The IVR baseline: a traditional keypad menu (FR-10-06; analysis Q-5, D-13, ADR-0009).

The menu is built from the business profile, like a small business would record it:
bookings, opening hours, contact details, services and prices, policies and FAQs, and an
operator. Bookings are entered on the keypad and go through the same `BookingService` and
rules as AgentIQ. It cannot search documents, understand free speech, judge mood or take a
booking reference with letters in it; for those the caller asks for the operator.

The simulated caller knows what they want (the scenario's expectations) and picks the menu
options a sensible caller would. A caller who reaches the operator has been handed to a
person, which ends the automated part of the call.
"""

import datetime as dt
import time
from collections.abc import Callable
from functools import partial

from app.bookings.availability import BookingRequest
from app.bookings.service import (
    BookingService,
    BookingUnavailableError,
    CustomerDetails,
    NewBooking,
)
from app.evaluation.environment import TURN_SECONDS, ScenarioEnv, spoken_seconds
from app.evaluation.results import Transcript
from app.evaluation.scenarios import (
    AnswerExpectation,
    BookingExpectation,
    IvrTopic,
    Scenario,
)
from app.knowledge.profile_facts import profile_facts
from app.profiles.schema import BookingMode, BusinessProfileConfig

KEY_SECONDS = 2.0  # time for the caller to press a key or type a short number
TOPIC_KEYS: dict[IvrTopic, str] = {"hours": "2", "contact": "3", "services": "4", "policies": "5"}
BACK = "To return to the main menu, press 9."
HOLD = "Please hold while we connect you to a member of our team."
NO_KEYPAD = "We need a few more details for this booking."

Goal = tuple[str, str | BookingExpectation]  # ("topic", key) | ("booking", e) | ("operator", "0")


def main_menu(config: BusinessProfileConfig) -> str:
    return (
        f"Welcome to {config.name}. For bookings, press 1. For opening hours, press 2. "
        "For our address and contact details, press 3. For services and prices, press 4. "
        "For policies and frequently asked questions, press 5. To speak to a member of our "
        "team, press 0."
    )


def topic_message(config: BusinessProfileConfig, key: str) -> str:
    if key == "2":
        return profile_facts(config)[0]  # the opening hours come first
    if key == "3":
        contact = config.contact
        parts = [
            f"{label} {value}."
            for label, value in (
                ("Our address is", contact.address),
                ("Our phone number is", contact.phone),
                ("Our email is", contact.email),
            )
            if value
        ]
        return " ".join(parts) or "Please call us during opening hours."
    if key == "4":
        lines = []
        for service in config.services:
            price = f", {config.currency} {service.price:,.0f}" if service.price is not None else ""
            lines.append(f"{service.name}{price}.")
        return "Our services: " + " ".join(lines)
    texts = [f"{p.title}: {p.text}" for p in config.policies]
    texts += [f"{f.question} {f.answer}" for f in config.faqs]
    return " ".join(texts) or "There are no policies on record."


def plan(scenario: Scenario) -> list[Goal]:
    """The menu options the caller goes through, in order: topics first, then bookings,
    then the operator if anything is left that no menu covers."""
    goals: list[Goal] = []
    needs_operator = False
    for item in scenario.expect:
        if isinstance(item, AnswerExpectation) and item.topic is not None:
            goal: Goal = ("topic", TOPIC_KEYS[item.topic])
            if goal not in goals:
                goals.append(goal)
        elif isinstance(item, BookingExpectation):
            goals.append(("booking", item))
        else:
            needs_operator = True
    goals.sort(key=lambda g: g[0] != "topic")
    if needs_operator:
        goals.append(("operator", "0"))
    return goals


class IvrSystem:
    name = "ivr"
    label = "IVR baseline (keypad menu)"

    async def run(self, scenario: Scenario, env: ScenarioEnv) -> Transcript:
        call = _Call(env, scenario, Transcript())
        call.speak(main_menu(env.config), latency_ms=None)
        goals = plan(scenario)
        for index, (kind, target) in enumerate(goals):
            if kind == "topic":
                assert isinstance(target, str)  # noqa: S101
                call.press(target, partial(_topic_reply, env.config, target))
            elif kind == "booking":
                assert isinstance(target, BookingExpectation)  # noqa: S101
                await call.book(target)
            else:
                call.press("0", lambda: HOLD)
                call.transcript.handed_off = True
            if call.transcript.handed_off:
                break
            if index < len(goals) - 1:
                call.press("9", lambda: main_menu(env.config))
        return call.transcript


class _Call:
    def __init__(self, env: ScenarioEnv, scenario: Scenario, transcript: Transcript) -> None:
        self.env = env
        self.scenario = scenario
        self.transcript = transcript

    def speak(self, text: str, latency_ms: float | None) -> None:
        self.transcript.say("system", text, latency_ms)
        self.transcript.est_call_seconds += spoken_seconds(text) + (latency_ms or 0) / 1000

    def caller(self, keys: str) -> None:
        self.env.clock.advance(TURN_SECONDS)
        self.transcript.say("customer", f"[keys] {keys}")
        self.transcript.est_call_seconds += KEY_SECONDS

    def press(self, keys: str, respond: Callable[[], str]) -> None:
        self.caller(keys)
        started = time.perf_counter()
        text = respond()
        self.speak(text, _ms(started))

    async def book(self, goal: BookingExpectation) -> None:
        config = self.env.config
        overnight = config.booking_rules.mode is BookingMode.OVERNIGHT
        services = [s for s in config.services if _bookable(config, s.id)]
        menu = " ".join(f"For {s.name}, press {i}." for i, s in enumerate(services, start=1))
        self.press("1", lambda: menu)
        choice = next((i for i, s in enumerate(services, start=1) if s.id == goal.service_id), None)
        if choice is None:  # the service isn't on the menu: ask for a person
            self.press("0", lambda: HOLD)
            self.transcript.handed_off = True
            return
        self.press(
            str(choice),
            lambda: (
                "Please enter the date: two digits each for the day and month, four for the year."
            ),
        )
        if overnight:
            self.press(goal.date.strftime("%d%m%Y"), lambda: "Please enter the number of nights.")
            self.press(str(goal.nights), lambda: "Please enter the number of guests.")
        else:
            self.press(
                goal.date.strftime("%d%m%Y"),
                lambda: "Please enter the time in 24-hour format, for example 1930.",
            )
            assert goal.time is not None  # noqa: S101  # checked when the scenarios load
            self.press(goal.time.strftime("%H%M"), lambda: "Please enter the number of people.")
        self.caller(str(goal.party_size))

        started = time.perf_counter()
        # A keypad can't take an email address or free text such as a reason for visit.
        extra: list[str] = [
            d for d in config.booking_rules.required_details if d not in {"name", "phone"}
        ]
        extra += [f.key for f in config.booking_rules.custom_fields if f.required]
        if extra:
            self.speak(f"{NO_KEYPAD} {HOLD}", _ms(started))
            self.transcript.handed_off = True
            return
        start = dt.datetime.combine(goal.date, goal.time or dt.time())
        request = BookingRequest(goal.service_id, goal.party_size, start, goal.nights)
        async with self.env.shared.session_factory() as db:
            availability = await BookingService(db, self.env.clock).check(self.env.profile, request)
        if not availability.check.available:
            self.speak(f"Sorry, that is not available. {BACK}", _ms(started))
            return
        when = (
            f"for {goal.nights} nights from {goal.date:%d %B}"
            if overnight
            else f"on {goal.date:%d %B} at {start:%H:%M}"
        )
        service_name = services[choice - 1].name
        self.speak(
            f"{service_name} {when} for {goal.party_size}. "
            "To confirm, press 1. To cancel, press 2.",
            _ms(started),
        )

        self.caller("1")
        started = time.perf_counter()
        phone = self.scenario.caller.phone
        try:
            async with self.env.shared.session_factory() as db:
                booking = await BookingService(db, self.env.clock).create(
                    self.env.profile,
                    NewBooking(
                        request=request,
                        # An IVR records the caller's name as a voice clip; this stands in.
                        customer=CustomerDetails(name=f"Phone caller {phone[-4:]}", phone=phone),
                    ),
                    actor="customer",
                    source="ivr",
                )
        except BookingUnavailableError:
            self.speak(f"Sorry, that was just taken. {BACK}", _ms(started))
            return
        spelled = " ".join(booking.reference)
        self.speak(f"Your booking is confirmed. Your reference is {spelled}. {BACK}", _ms(started))


def _topic_reply(config: BusinessProfileConfig, key: str) -> str:
    return f"{topic_message(config, key)} {BACK}"


def _bookable(config: BusinessProfileConfig, service_id: str) -> bool:
    return any(not r.service_ids or service_id in r.service_ids for r in config.resources)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
