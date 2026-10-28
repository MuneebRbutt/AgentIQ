"""Business-profile validation (FR-08-05, ADR-0002)."""

import copy
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.profiles.examples import _raw_example, load_example
from app.profiles.schema import BookingMode, BusinessProfileConfig, Domain, Weekday


@pytest.mark.parametrize("domain", list(Domain))
def test_every_example_profile_is_valid(domain: Domain) -> None:
    config = load_example(domain)

    assert config.domain is domain
    assert config.services and config.resources


def test_examples_cover_both_booking_modes() -> None:
    modes = {load_example(domain).booking_rules.mode for domain in Domain}

    assert modes == set(BookingMode)


def _restaurant() -> dict[str, Any]:
    return copy.deepcopy(_raw_example(Domain.RESTAURANT))


def _errors(data: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    with pytest.raises(ValidationError) as exc_info:
        BusinessProfileConfig.model_validate(data)
    return [(tuple(err["loc"]), err["msg"]) for err in exc_info.value.errors()]


@pytest.mark.parametrize(
    "field",
    ["name", "domain", "timezone", "opening_hours", "services", "resources", "booking_rules"],
)
def test_tc08_missing_mandatory_field_is_reported_by_name(field: str) -> None:
    data = _restaurant()
    del data[field]

    errors = _errors(data)

    assert ((field,), "Field required") in errors


def test_blank_domain_is_rejected() -> None:
    data = _restaurant()
    data["domain"] = ""

    assert _errors(data)[0][0] == ("domain",)


def test_unknown_keys_are_rejected() -> None:
    data = _restaurant()
    data["booking_rules"]["slot_minute"] = 30  # typo

    assert _errors(data)[0][0] == ("booking_rules", "slot_minute")


def _mutate(change: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    data = _restaurant()
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "location", "message"),
    [
        (lambda d: d.update(timezone="Mars/Olympus"), ("timezone",), "unknown time zone"),
        (lambda d: d.update(opening_hours={}), ("opening_hours",), "at least one day"),
        (
            lambda d: d["opening_hours"].update(
                monday=[
                    {"opens": "12:00", "closes": "16:00"},
                    {"opens": "15:00", "closes": "22:00"},
                ]
            ),
            ("opening_hours",),
            "overlap on monday",
        ),
        (
            lambda d: d["opening_hours"].update(monday=[{"opens": "12:00", "closes": "12:00"}]),
            ("opening_hours", "monday", 0),
            "must differ",
        ),
        (lambda d: d.update(services=[]), ("services",), "at least 1"),
        (
            lambda d: d["services"].append(dict(d["services"][0])),
            ("services",),
            "duplicate service id: dine-in",
        ),
        (
            lambda d: d["resources"].append(dict(d["resources"][0])),
            ("resources",),
            "duplicate resource id: table-2",
        ),
        (
            lambda d: d["resources"][0].update(service_ids=["takeaway"]),
            ("resources",),
            "unknown service(s): takeaway",
        ),
        (
            lambda d: d["resources"][0].update(quantity=0),
            ("resources", 0, "quantity"),
            "greater than or equal to 1",
        ),
        (lambda d: d["services"][0].update(id="Dine In"), ("services", 0, "id"), "pattern"),
        (lambda d: d.update(currency="rupees"), ("currency",), "pattern"),
        (lambda d: d["contact"].update(phone="call me"), ("contact", "phone"), "pattern"),
    ],
    ids=[
        "timezone",
        "never-open",
        "overlapping-hours",
        "empty-interval",
        "no-services",
        "duplicate-service",
        "duplicate-resource",
        "unknown-service-ref",
        "zero-quantity",
        "bad-slug",
        "bad-currency",
        "bad-phone",
    ],
)
def test_invalid_profiles_are_rejected_with_a_located_reason(
    change: Callable[[dict[str, Any]], object], location: tuple[str | int, ...], message: str
) -> None:
    errors = _errors(_mutate(change))

    assert any(loc == location and message in msg for loc, msg in errors), errors


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"slot_minutes": None}, "slot_minutes is required"),
        ({"check_in_time": "14:00"}, "only apply to overnight"),
        ({"min_party_size": 6, "max_party_size": 4}, "min_party_size can't be larger"),
        ({"required_details": ["name", "name"]}, "duplicate required detail"),
        (
            {"custom_fields": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]},
            "duplicate custom field key",
        ),
    ],
)
def test_time_slot_booking_rules(rules: dict[str, Any], message: str) -> None:
    data = _restaurant()
    data["booking_rules"].update(rules)

    errors = _errors(data)

    assert any(loc == ("booking_rules",) and message in msg for loc, msg in errors), errors


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"check_out_time": None}, "required for overnight"),
        ({"slot_minutes": 30}, "only apply to time_slot"),
    ],
)
def test_overnight_booking_rules(rules: dict[str, Any], message: str) -> None:
    data = copy.deepcopy(_raw_example(Domain.HOTEL))
    data["booking_rules"].update(rules)

    errors = _errors(data)

    assert any(loc == ("booking_rules",) and message in msg for loc, msg in errors), errors


def test_time_slot_services_need_a_duration() -> None:
    data = _restaurant()
    del data["services"][1]["duration_minutes"]

    errors = _errors(data)
    assert any("private-dining" in msg for _, msg in errors), errors

    data["booking_rules"]["default_duration_minutes"] = 60
    BusinessProfileConfig.model_validate(data)


def test_overnight_hours_and_sorting() -> None:
    data = _restaurant()
    data["opening_hours"]["monday"] = [
        {"opens": "19:00", "closes": "01:00"},
        {"opens": "12:00", "closes": "15:00"},
    ]

    config = BusinessProfileConfig.model_validate(data)

    monday = config.opening_hours[Weekday.MONDAY]
    assert [interval.opens.hour for interval in monday] == [12, 19]
    assert monday[1].crosses_midnight


def test_round_trips_through_json() -> None:
    config = load_example(Domain.CLINIC)

    restored = BusinessProfileConfig.model_validate(config.model_dump(mode="json"))

    assert restored == config
