"""Evaluation scenarios and their ground truth (FR-10-01…04, analysis M-16; ADR-0009).

One YAML file per domain in `eval/scenarios/`. Each scenario is a scripted customer: what
they say, who they are, and what a correct system achieves (`expect`). The same scenarios
run against AgentIQ and every baseline, so the comparison is like for like (FR-10-06).
"""

import datetime as dt
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.profiles.examples import load_example
from app.profiles.schema import BookingMode, BusinessProfileConfig, Domain

REFERENCE_PLACEHOLDER = "{reference}"

Category = Literal[
    "knowledge", "booking", "cancellation", "multi_intent", "complaint", "out_of_scope"
]
CATEGORIES: tuple[Category, ...] = (
    "knowledge",
    "booking",
    "cancellation",
    "multi_intent",
    "complaint",
    "out_of_scope",
)
# Where an IVR caller would find an answer; `None` means no menu has it.
IvrTopic = Literal["hours", "contact", "services", "policies"]
Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,40}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Caller(_Strict):
    name: str
    phone: str
    email: str | None = None
    # The profile's extra booking fields (`custom_fields`), by key, e.g. a clinic's
    # patient_age; the caller gives them when asked and the setup booking carries them.
    details: dict[str, str] = Field(default_factory=dict)


class SetupBooking(_Strict):
    """A booking that exists before the call, for change and cancellation scenarios."""

    service_id: str
    party_size: int = Field(ge=1)
    date: dt.date
    time: dt.time | None = None
    nights: int | None = Field(default=None, ge=1)


class AnswerExpectation(_Strict):
    """A question the business's information answers. Every fact must appear in what the
    system said, in any of its listed forms, and nothing in `forbid` may (FR-10-03)."""

    kind: Literal["answer"]
    id: Id
    facts: list[Annotated[list[str], Field(min_length=1)]] = Field(min_length=1)
    forbid: list[str] = Field(default_factory=list)
    topic: IvrTopic | None = None


class RefusalExpectation(_Strict):
    """A question the business's information doesn't answer: the system must say so (or
    pass the customer to a person) instead of inventing an answer (FR-05-05)."""

    kind: Literal["refusal"]
    id: Id
    forbid_patterns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _patterns_compile(self) -> "RefusalExpectation":
        for pattern in self.forbid_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"forbid pattern {pattern!r} is not a valid regex: {exc}") from exc
        return self


class BookingExpectation(_Strict):
    """A booking that must exist, confirmed, when the conversation ends (FR-04)."""

    kind: Literal["booking"]
    id: Id
    service_id: str
    party_size: int = Field(ge=1)
    date: dt.date
    time: dt.time | None = None
    nights: int | None = Field(default=None, ge=1)


class CancelExpectation(_Strict):
    """The scenario's setup booking must be cancelled (FR-04-05)."""

    kind: Literal["cancelled"]
    id: Id


class EscalationExpectation(_Strict):
    """The conversation must be handed to a person (FR-06-04)."""

    kind: Literal["escalation"]
    id: Id


Expectation = Annotated[
    AnswerExpectation
    | RefusalExpectation
    | BookingExpectation
    | CancelExpectation
    | EscalationExpectation,
    Field(discriminator="kind"),
]
KNOWLEDGE_KINDS = frozenset({"answer", "refusal"})


class Scenario(_Strict):
    id: Id
    title: str
    category: Category
    start: dt.datetime  # local wall-clock time at the business; the clock is fixed to it
    caller: Caller
    turns: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)
    expect: list[Expectation] = Field(min_length=1)
    setup_booking: SetupBooking | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Scenario":
        if self.start.tzinfo is not None:
            raise ValueError("start is local time at the business: leave out the offset")
        ids = [item.id for item in self.expect]
        if len(ids) != len(set(ids)):
            raise ValueError("expectation ids must be unique within a scenario")
        uses_reference = any(REFERENCE_PLACEHOLDER in turn for turn in self.turns)
        cancels = any(item.kind == "cancelled" for item in self.expect)
        if (uses_reference or cancels) and self.setup_booking is None:
            raise ValueError("a scenario that refers to a booking needs setup_booking")
        return self


class DomainSet(_Strict):
    domain: Domain
    documents: list[str] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(min_length=1)


class ScenarioFileError(ValueError):
    """A scenario file doesn't fit the profile it is written for."""


def load_domain_set(path: Path) -> DomainSet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    domain_set = DomainSet.model_validate(raw)
    check_against_profile(domain_set, load_example(domain_set.domain), path.parent.parent)
    return domain_set


def load_scenarios(eval_dir: Path) -> list[DomainSet]:
    """Every domain's scenarios, checked; scenario ids are unique across all files."""
    sets = [load_domain_set(path) for path in sorted((eval_dir / "scenarios").glob("*.yaml"))]
    seen: set[str] = set()
    for domain_set in sets:
        for scenario in domain_set.scenarios:
            if scenario.id in seen:
                raise ScenarioFileError(f"scenario id {scenario.id} is used twice")
            seen.add(scenario.id)
    return sets


def document_path(eval_dir: Path, domain: Domain, filename: str) -> Path:
    return eval_dir / "documents" / domain.value / filename


def check_against_profile(
    domain_set: DomainSet, config: BusinessProfileConfig, eval_dir: Path
) -> None:
    """Catches ground truth that can't be right for the profile (wrong service ids, a time
    on a by-night profile, a missing document) before any system is run on it."""
    services = {service.id for service in config.services}
    required_fields = {f.key for f in config.booking_rules.custom_fields if f.required}
    known_fields = {f.key for f in config.booking_rules.custom_fields}
    overnight = config.booking_rules.mode is BookingMode.OVERNIGHT
    for filename in domain_set.documents:
        if not document_path(eval_dir, domain_set.domain, filename).is_file():
            raise ScenarioFileError(f"{domain_set.domain.value}: document {filename} is missing")
    for scenario in domain_set.scenarios:
        bookings: list[SetupBooking | BookingExpectation] = [
            item for item in scenario.expect if isinstance(item, BookingExpectation)
        ]
        if scenario.setup_booking is not None:
            bookings.append(scenario.setup_booking)
        if unknown := set(scenario.caller.details) - known_fields:
            raise ScenarioFileError(f"{scenario.id}: unknown caller details {sorted(unknown)}")
        if bookings and (missing := required_fields - set(scenario.caller.details)):
            raise ScenarioFileError(f"{scenario.id}: caller needs details {sorted(missing)}")
        if (
            bookings
            and "email" in config.booking_rules.required_details
            and not (scenario.caller.email)
        ):
            raise ScenarioFileError(f"{scenario.id}: this profile needs the caller's email")
        for booking in bookings:
            where = f"{scenario.id}: booking for {booking.service_id}"
            if booking.service_id not in services:
                raise ScenarioFileError(f"{where}: unknown service")
            if overnight and (booking.nights is None or booking.time is not None):
                raise ScenarioFileError(f"{where}: this profile books by night (nights, no time)")
            if not overnight and (booking.time is None or booking.nights is not None):
                raise ScenarioFileError(f"{where}: this profile books by time (time, no nights)")
