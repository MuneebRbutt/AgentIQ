"""The Booking agent end to end against a real database (FR-04, UC-04, TC-04)."""

import datetime as dt
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.booking.agent import BookingAction, BookingAgent, BookingSubtask
from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.bookings.models import Booking
from app.bookings.service import BookingService
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.tenancy.models import Tenant
from tests.integration.conftest import FakeClock, OperatorFactory, next_tuesday

pytestmark = pytest.mark.integration

MakeProfile = Callable[[Tenant, Domain], Awaitable[BusinessProfile]]

# What a customer asks for in each domain: same agent, same code, different profile.
REQUESTS: dict[Domain, dict[str, object]] = {
    Domain.RESTAURANT: {"service_id": "dine-in", "party_size": 2, "time": dt.time(19, 0)},
    Domain.CLINIC: {
        "service_id": "general-consultation",
        "time": dt.time(10, 0),
        "details": {"patient_age": "34", "reason_for_visit": "Fever"},
    },
    Domain.HOTEL: {"party_size": 2, "nights": 2, "customer_email": "guest@example.com"},
}


class Harness:
    def __init__(self, db: AsyncSession, clock: FakeClock, profile: BusinessProfile) -> None:
        self.agent = BookingAgent(BookingService(db, clock))
        self.profile = profile
        self.day = next_tuesday(clock)

    async def run(self, phone: str | None = None, **fields: object) -> AgentResult:
        context = AgentContext(self.profile.tenant_id, self.profile, customer_phone=phone)
        return await self.agent.run(context, BookingSubtask.model_validate(fields))

    async def book(self, domain: Domain, phone: str, **overrides: object) -> AgentResult:
        fields = {
            "action": "create",
            "date": self.day,
            "customer_name": "Test Customer",
            "confirmed": True,
            **REQUESTS[domain],
            **overrides,
        }
        return await self.run(phone, **fields)


@pytest.fixture
async def harness_for(
    db: AsyncSession, clock: FakeClock, factory: OperatorFactory, make_profile: MakeProfile
) -> Callable[[Domain], Awaitable[Harness]]:
    async def make(domain: Domain) -> Harness:
        profile = await make_profile(await factory.tenant(), domain)
        return Harness(db, clock, profile)

    return make


@pytest.mark.parametrize("domain", list(Domain))
async def test_tc04_create_reject_when_full_then_cancel(
    domain: Domain, harness_for: Callable[[Domain], Awaitable[Harness]], db: AsyncSession
) -> None:
    harness = await harness_for(domain)

    # Step 1: an available slot is read back first, then booked once confirmed (FR-04-03).
    preview = await harness.book(domain, "+92 300 0000001", confirmed=False)
    assert preview.status is AgentStatus.NEEDS_CONFIRMATION, preview.message
    created = await harness.book(domain, "+92 300 0000001")
    assert created.status is AgentStatus.COMPLETED, created.message
    reference = created.data["reference"]
    assert reference in created.message

    # Step 2: fill the slot, then the same request is declined with alternatives.
    result = created
    for n in range(2, 200):
        result = await harness.book(domain, f"+92 300 {n:07d}")
        if result.status is not AgentStatus.COMPLETED:
            break
    assert result.status is AgentStatus.UNAVAILABLE
    assert result.data["reason"] == "no_capacity"
    assert result.alternatives, result.message
    assert "Nearest options" in result.message

    # Step 3: the customer cancels the first booking with its reference and phone.
    cancel = {"action": "cancel", "reference": reference, "customer_phone": "0300-0000001"}
    confirm = await harness.run(**cancel)
    assert confirm.status is AgentStatus.NEEDS_CONFIRMATION
    cancelled = await harness.run(**cancel, confirmed=True)
    assert cancelled.status is AgentStatus.COMPLETED, cancelled.message
    assert cancelled.data["status"] == "cancelled"

    # Post-condition: the database shows the booking created then cancelled.
    booking = await db.get(Booking, created.data["booking_id"])
    assert booking is not None
    await db.refresh(booking)
    assert booking.status == "cancelled"


async def test_asks_for_what_is_missing(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    clinic = await harness_for(Domain.CLINIC)

    result = await clinic.run(action="create", date=clinic.day)

    assert result.status is AgentStatus.NEEDS_INFO
    # Party size isn't asked: the clinic only ever books one patient.
    assert result.missing == [
        "service_id",
        "time",
        "customer_name",
        "customer_phone",
        "details.patient_age",
        "details.reason_for_visit",
    ]


async def test_caller_id_supplies_the_phone_number(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    restaurant = await harness_for(Domain.RESTAURANT)

    result = await restaurant.book(Domain.RESTAURANT, "+92 321 5550000")
    lookup = await restaurant.run(
        "+92 321 5550000", action="cancel", reference=result.data["reference"]
    )

    assert result.status is AgentStatus.COMPLETED
    assert lookup.status is AgentStatus.NEEDS_CONFIRMATION


async def test_single_service_profiles_do_not_ask_which_service(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    hotel = await harness_for(Domain.HOTEL)

    result = await hotel.run(action="check", date=hotel.day, party_size=2, nights=1)

    assert result.status is AgentStatus.COMPLETED
    assert result.data["service_id"] == "room-stay"
    assert "1 night from" in result.message


async def test_check_reports_closed_with_alternatives(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    restaurant = await harness_for(Domain.RESTAURANT)

    result = await restaurant.run(
        action="check", service_id="dine-in", party_size=2, date=restaurant.day, time=dt.time(17, 0)
    )

    assert result.status is AgentStatus.UNAVAILABLE
    assert result.data["reason"] == "closed"
    assert len(result.alternatives) == 3


async def test_wrong_phone_cannot_touch_someone_elses_booking(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    restaurant = await harness_for(Domain.RESTAURANT)
    created = await restaurant.book(Domain.RESTAURANT, "+92 300 1111111")

    result = await restaurant.run(
        "+92 300 2222222", action="cancel", reference=created.data["reference"], confirmed=True
    )

    assert result.status is AgentStatus.REJECTED
    assert result.message == "No booking matches that reference and phone number."


async def test_cancellation_inside_the_notice_window_quotes_the_policy(
    harness_for: Callable[[Domain], Awaitable[Harness]], clock: FakeClock
) -> None:
    hotel = await harness_for(Domain.HOTEL)
    created = await hotel.book(Domain.HOTEL, "+92 300 1111111")
    # Move to 24 hours before check-in; the hotel needs 48 hours' notice.
    check_in = dt.datetime.fromisoformat(created.data["starts_at"])
    clock.current = check_in - dt.timedelta(hours=24)

    result = await hotel.run(
        "+92 300 1111111", action="cancel", reference=created.data["reference"], confirmed=True
    )

    assert result.status is AgentStatus.REJECTED
    assert "48 hours" in result.message
    assert "Free cancellation up to 48 hours" in result.message  # the profile's own policy


async def test_modify_moves_the_booking_after_confirmation(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    restaurant = await harness_for(Domain.RESTAURANT)
    created = await restaurant.book(Domain.RESTAURANT, "+92 300 1111111")
    change = {
        "action": "modify",
        "reference": created.data["reference"],
        "customer_phone": "+92 300 1111111",
        "time": dt.time(20, 30),
        "party_size": 4,
    }

    nothing = await restaurant.run(**{**change, "time": None, "party_size": None})
    preview = await restaurant.run(**change)
    changed = await restaurant.run(**change, confirmed=True)

    assert nothing.status is AgentStatus.NEEDS_INFO
    assert preview.status is AgentStatus.NEEDS_CONFIRMATION
    assert changed.status is AgentStatus.COMPLETED, changed.message
    assert changed.data["party_size"] == 4
    assert changed.data["resource_id"] == "table-4"
    assert "20:30" in changed.message


async def test_customers_book_only_within_the_notice_rules(
    harness_for: Callable[[Domain], Awaitable[Harness]],
) -> None:
    clinic = await harness_for(Domain.CLINIC)

    result = await clinic.book(Domain.CLINIC, "+92 300 1111111", time=dt.time(10, 3))

    assert result.status is AgentStatus.UNAVAILABLE
    assert result.data["reason"] == "not_on_slot"


def test_actions_are_the_documented_set() -> None:
    assert {action.value for action in BookingAction} == {"check", "create", "modify", "cancel"}
