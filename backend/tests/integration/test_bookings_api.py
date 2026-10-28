"""Booking endpoints for the dashboard (FR-04, FR-09-01/02/04, TC-09 step 1)."""

import asyncio
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.tenancy.models import Tenant
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory, next_tuesday

pytestmark = pytest.mark.integration

MakeProfile = Callable[[Tenant, Domain], Awaitable[BusinessProfile]]


class Dashboard:
    def __init__(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        profile: BusinessProfile,
        day: dt.date,
    ) -> None:
        self.client = client
        self.headers = headers
        self.base = f"/api/v1/profiles/{profile.id}"
        self.day = day
        self.email = "owner@spice.example.com"

    async def create(self, **overrides: Any) -> httpx.Response:
        body = {
            "service_id": "dine-in",
            "party_size": 2,
            "start": f"{self.day}T19:00",
            "customer": {"name": "Ali Raza", "phone": "+92 300 1234567"},
            **overrides,
        }
        return await self.client.post(f"{self.base}/bookings", json=body, headers=self.headers)

    async def relogin(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login", json={"email": self.email, "password": DEFAULT_PASSWORD}
        )
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def get(self, path: str = "", **params: Any) -> httpx.Response:
        return await self.client.get(f"{self.base}{path}", params=params, headers=self.headers)


async def _login(
    client: httpx.AsyncClient, factory: OperatorFactory, tenant: Tenant, email: str
) -> dict[str, str]:
    await factory.operator(email, tenant=tenant)
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def dashboard(
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    make_profile: MakeProfile,
    clock: FakeClock,
) -> Dashboard:
    tenant = await factory.tenant()
    headers = await _login(live_client, factory, tenant, "owner@spice.example.com")
    profile = await make_profile(tenant, Domain.RESTAURANT)
    return Dashboard(live_client, headers, profile, next_tuesday(clock))


async def test_tc09_booking_is_persisted_and_retrievable(dashboard: Dashboard) -> None:
    created = await dashboard.create(details={"occasion": "Birthday"}, notes="Window seat")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "confirmed"
    assert body["resource_id"] == "table-2"
    assert body["source"] == "dashboard"
    assert len(body["reference"]) == 6
    assert body["customer"]["name"] == "Ali Raza"
    assert dt.datetime.fromisoformat(body["starts_at"]).utcoffset() is not None

    fetched = await dashboard.get(f"/bookings/{body['id']}")
    assert fetched.json() == body

    listed = await dashboard.get("/bookings", status="confirmed")
    assert [item["id"] for item in listed.json()] == [body["id"]]

    events = (await dashboard.get(f"/bookings/{body['id']}/events")).json()
    assert [event["kind"] for event in events] == ["created"]
    assert events[0]["actor"].startswith("operator:")


async def test_availability_endpoint(dashboard: Dashboard) -> None:
    free = (
        await dashboard.get(
            "/availability", service_id="dine-in", start=f"{dashboard.day}T19:00", party_size=6
        )
    ).json()
    closed = (
        await dashboard.get("/availability", service_id="dine-in", start=f"{dashboard.day}T17:00")
    ).json()

    assert free["available"] is True
    assert free["slot"]["resource_id"] == "table-8"
    assert closed == {
        "available": False,
        "reason": "closed",
        "slot": None,
        "alternatives": closed["alternatives"],
    }
    assert len(closed["alternatives"]) == 3


async def test_full_slot_returns_409_with_alternatives(dashboard: Dashboard) -> None:
    for n in range(3):  # all three tables for 8
        response = await dashboard.create(
            party_size=8, customer={"name": f"Guest {n}", "phone": f"+92 300 000000{n}"}
        )
        assert response.status_code == 201

    full = await dashboard.create(party_size=8)

    assert full.status_code == 409
    assert full.json()["code"] == "unavailable"
    assert full.json()["reason"] == "no_capacity"
    assert len(full.json()["alternatives"]) == 3


async def test_missing_required_details_are_named(dashboard: Dashboard) -> None:
    response = await dashboard.create(customer={"name": "", "phone": "+92 300 1234567"})

    assert response.status_code == 422
    assert response.json()["missing"] == ["name"]


async def test_operator_can_modify_and_cancel_even_inside_the_notice_window(
    dashboard: Dashboard, clock: FakeClock
) -> None:
    created = (await dashboard.create()).json()
    url = f"{dashboard.base}/bookings/{created['id']}"
    clock.current = dt.datetime.fromisoformat(created["starts_at"]) - dt.timedelta(hours=1)
    await dashboard.relogin()  # the clock jump expired the old token

    moved = await dashboard.client.patch(
        url, json={"start": f"{dashboard.day}T20:00", "party_size": 3}, headers=dashboard.headers
    )
    cancelled = await dashboard.client.post(f"{url}/cancel", headers=dashboard.headers)
    again = await dashboard.client.post(f"{url}/cancel", headers=dashboard.headers)

    assert moved.status_code == 200, moved.text
    assert moved.json()["resource_id"] == "table-4"
    assert cancelled.json()["status"] == "cancelled"
    assert again.status_code == 409
    events = (await dashboard.get(f"/bookings/{created['id']}/events")).json()
    assert [event["kind"] for event in events] == ["created", "modified", "cancelled"]
    assert set(events[1]["changes"]) >= {"party_size", "starts_at", "resource_id"}


async def test_cancelled_booking_frees_its_unit(dashboard: Dashboard) -> None:
    created = [
        (
            await dashboard.create(
                party_size=8, customer={"name": "G", "phone": f"+92 300 000000{n}"}
            )
        ).json()
        for n in range(3)
    ]
    await dashboard.client.post(
        f"{dashboard.base}/bookings/{created[0]['id']}/cancel", headers=dashboard.headers
    )

    assert (await dashboard.create(party_size=8)).status_code == 201


async def test_simultaneous_requests_cannot_overbook(dashboard: Dashboard) -> None:
    for n in range(2):  # leave one table for 8
        await dashboard.create(party_size=8, customer={"name": "G", "phone": f"+92 300 000000{n}"})

    results = await asyncio.gather(
        *(
            dashboard.create(party_size=8, customer={"name": "G", "phone": f"+92 301 000000{n}"})
            for n in range(4)
        )
    )

    assert sorted(response.status_code for response in results) == [201, 409, 409, 409]


async def test_same_phone_is_one_customer(dashboard: Dashboard) -> None:
    first = (await dashboard.create(customer={"name": "Ali", "phone": "+92 300 1234567"})).json()
    second = (
        await dashboard.create(
            start=f"{dashboard.day}T21:00", customer={"name": "Ali Raza", "phone": "0300-1234567"}
        )
    ).json()

    assert first["customer"]["id"] == second["customer"]["id"]
    assert second["customer"]["name"] == "Ali Raza"


async def test_other_businesses_bookings_are_invisible(
    dashboard: Dashboard,
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    make_profile: MakeProfile,
) -> None:
    created = (await dashboard.create()).json()
    other_tenant = await factory.tenant("City Clinic")
    other_headers = await _login(live_client, factory, other_tenant, "owner@clinic.example.com")
    other_profile = await make_profile(other_tenant, Domain.CLINIC)

    # NFR03-02: someone else's profile, and someone else's booking under your own profile.
    foreign_profile = await live_client.get(f"{dashboard.base}/bookings", headers=other_headers)
    foreign_booking = await live_client.get(
        f"/api/v1/profiles/{other_profile.id}/bookings/{created['id']}", headers=other_headers
    )
    missing = await dashboard.get(f"/bookings/{uuid.uuid4()}")

    assert foreign_profile.status_code == foreign_booking.status_code == missing.status_code == 404
