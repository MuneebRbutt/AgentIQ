"""Availability rules (FR-04-02) for all three example domains, without a database."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.bookings.availability import AvailabilityChecker, BookingRequest, Period, Unavailable
from app.profiles.examples import load_example
from app.profiles.schema import BusinessProfileConfig, Domain

KARACHI = ZoneInfo("Asia/Karachi")
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=KARACHI)  # a Sunday
TUESDAY = datetime(2026, 10, 20)


def at(hour: int, minute: int = 0, day: datetime = TUESDAY) -> datetime:
    return day.replace(hour=hour, minute=minute)


def booked(start: datetime, minutes: int) -> Period:
    begins = start.replace(tzinfo=KARACHI)
    return Period(begins, begins + timedelta(minutes=minutes))


def checker(
    domain: Domain,
    taken: dict[str, list[Period]] | None = None,
    profile: BusinessProfileConfig | None = None,
) -> AvailabilityChecker:
    return AvailabilityChecker(profile or load_example(domain), taken or {}, NOW)


def dine_in(start: datetime, party: int = 2) -> BookingRequest:
    return BookingRequest("dine-in", party, start)


def test_free_table_is_the_smallest_that_fits() -> None:
    restaurant = checker(Domain.RESTAURANT)

    two = restaurant.check(dine_in(at(19), 2))
    three = restaurant.check(dine_in(at(19), 3))

    assert (two.available, two.resource_id) == (True, "table-2")
    assert three.resource_id == "table-4"
    period, _ = two.slot()
    assert period.starts_at == datetime(2026, 10, 20, 19, 0, tzinfo=KARACHI)
    assert period.ends_at - period.starts_at == timedelta(minutes=90)  # service duration
    assert period.starts_at.utcoffset() == timedelta(hours=5)


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (BookingRequest("takeaway", 2, at(19)), Unavailable.UNKNOWN_SERVICE),
        (dine_in(at(19), 9), Unavailable.PARTY_SIZE),  # biggest dine-in table seats 8
        (dine_in(at(19), 21), Unavailable.PARTY_SIZE),  # above max_party_size
        (dine_in(at(19, 10)), Unavailable.NOT_ON_SLOT),
        (dine_in(at(16)), Unavailable.CLOSED),  # between lunch and dinner
        (dine_in(at(15)), Unavailable.CLOSED),  # would run past lunch closing
        (dine_in(at(19, 0, TUESDAY + timedelta(days=61))), Unavailable.TOO_FAR_AHEAD),
    ],
    ids=[
        "unknown-service",
        "no-table-big-enough",
        "over-max-party",
        "off-grid",
        "closed",
        "runs-past-closing",
        "too-far",
    ],
)
def test_restaurant_rules(request_: BookingRequest, reason: Unavailable) -> None:
    assert checker(Domain.RESTAURANT).check(request_).reason is reason


def test_too_soon_respects_minimum_notice() -> None:
    profile = load_example(Domain.RESTAURANT)
    early = AvailabilityChecker(profile, {}, datetime(2026, 10, 20, 18, 30, tzinfo=KARACHI))

    assert early.check(dine_in(at(19))).reason is Unavailable.TOO_SOON
    assert early.check(dine_in(at(19, 30))).available


def test_interval_past_midnight_belongs_to_the_day_it_opens() -> None:
    friday = datetime(2026, 10, 23)
    restaurant = checker(Domain.RESTAURANT)

    assert restaurant.check(dine_in(at(23, 0, friday))).available  # ends 00:30, closing time
    assert restaurant.check(dine_in(at(23, 30, friday))).reason is Unavailable.CLOSED


def test_full_size_moves_up_then_runs_out() -> None:
    two_tables = [booked(at(19), 90)] * 8
    restaurant = checker(Domain.RESTAURANT, {"table-2": two_tables})

    assert restaurant.check(dine_in(at(19))).resource_id == "table-4"

    everything = {
        "table-2": two_tables,
        "table-4": [booked(at(19), 90)] * 10,
        "table-8": [booked(at(19), 90)] * 3,
    }
    assert checker(Domain.RESTAURANT, everything).check(dine_in(at(19))).reason is (
        Unavailable.NO_CAPACITY
    )


def test_back_to_back_bookings_do_not_clash() -> None:
    clinic = checker(Domain.CLINIC, {"dr-ayesha": [booked(at(10), 15)]})

    assert clinic.check(BookingRequest("general-consultation", 1, at(10, 15))).available
    assert (
        clinic.check(BookingRequest("general-consultation", 1, at(10, 5))).reason
        is Unavailable.NO_CAPACITY
    )


def test_capacity_counts_peak_overlap_not_total_overlap() -> None:
    profile = load_example(Domain.RESTAURANT)
    profile.resources[0].quantity = 2  # two tables for 2
    # Both bookings overlap 19:00-20:30 but never each other: one table stays free.
    taken = {"table-2": [booked(at(18, 30), 60), booked(at(20), 90)]}

    result = checker(Domain.RESTAURANT, taken, profile).check(dine_in(at(19)))

    assert result.resource_id == "table-2"


def test_resources_only_serve_their_services() -> None:
    clinic = checker(Domain.CLINIC)

    paediatric = clinic.check(BookingRequest("paediatric-consultation", 1, at(10)))
    general = clinic.check(BookingRequest("general-consultation", 1, at(10)))

    assert paediatric.resource_id == "dr-hamza"
    assert general.resource_id == "dr-ayesha"
    assert clinic.check(BookingRequest("follow-up", 2, at(10))).reason is Unavailable.PARTY_SIZE


def test_clinic_closed_on_sunday() -> None:
    sunday = datetime(2026, 10, 25)
    result = checker(Domain.CLINIC).check(BookingRequest("follow-up", 1, at(10, 0, sunday)))

    assert result.reason is Unavailable.CLOSED


def test_overnight_stay_uses_check_in_and_check_out_times() -> None:
    hotel = checker(Domain.HOTEL)

    result = hotel.check(BookingRequest("room-stay", 2, TUESDAY, nights=3))

    period, resource = result.slot()
    assert resource == "standard-room"
    assert period.starts_at == datetime(2026, 10, 20, 14, 0, tzinfo=KARACHI)
    assert period.ends_at == datetime(2026, 10, 23, 12, 0, tzinfo=KARACHI)
    assert hotel.check(BookingRequest("room-stay", 4, TUESDAY, 1)).resource_id == "family-suite"
    assert hotel.check(BookingRequest("room-stay", 2, TUESDAY, 31)).reason is Unavailable.NIGHTS


def test_checkout_day_can_be_rebooked() -> None:
    stays = [
        Period(
            datetime(2026, 10, 19, 14, tzinfo=KARACHI), datetime(2026, 10, 20, 12, tzinfo=KARACHI)
        )
    ]
    hotel = checker(Domain.HOTEL, {"family-suite": stays * 10})

    assert hotel.check(BookingRequest("room-stay", 5, TUESDAY, 1)).available


def test_alternatives_are_the_nearest_free_slots() -> None:
    full = {
        "table-2": [booked(at(19), 90)] * 8,
        "table-4": [booked(at(19), 90)] * 10,
        "table-8": [booked(at(19), 90)] * 3,
    }
    restaurant = checker(Domain.RESTAURANT, full)

    alternatives = restaurant.alternatives(dine_in(at(19)))

    starts = [alt.slot()[0].starts_at.strftime("%H:%M") for alt in alternatives]
    assert starts == ["20:30", "21:00", "21:30"]


def test_overnight_alternatives_shift_the_check_in_date() -> None:
    suites = [
        Period(
            datetime(2026, 10, 20, 14, tzinfo=KARACHI), datetime(2026, 10, 21, 12, tzinfo=KARACHI)
        )
    ]
    hotel = checker(Domain.HOTEL, {"family-suite": suites * 10})
    request = BookingRequest("room-stay", 5, TUESDAY, 1)

    assert hotel.check(request).reason is Unavailable.NO_CAPACITY
    dates = [alt.slot()[0].starts_at.date().isoformat() for alt in hotel.alternatives(request, 2)]
    assert dates == ["2026-10-19", "2026-10-21"]
