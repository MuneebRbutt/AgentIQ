"""The dashboard overview of one profile (FR-08-03, FR-10-02)."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import FakeClock, OperatorFactory, next_tuesday
from tests.integration.test_escalations_api import FURIOUS, RecordingNotifier, _use_notifier
from tests.integration.test_sessions_api import PHONE, Console, _console

pytestmark = pytest.mark.integration


async def _analytics(console: Console, **params: Any) -> dict[str, Any]:
    response = await console.client.get(
        f"/api/v1/profiles/{console.profile.id}/analytics",
        params=params,
        headers=console.headers,
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


async def test_an_empty_profile_has_zeroes(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> None:
    console = await _console(live_client, factory, db)
    overview = await _analytics(console)
    assert overview["period_days"] == 30
    assert overview["conversations"] == {"total": 0, "needs_review": 0}
    assert overview["latency"] == {
        "replies": 0,
        "average_ms": None,
        "p50_ms": None,
        "p95_ms": None,
    }
    assert overview["agents"] == [] and overview["outcomes"] == {}


async def test_the_overview_counts_what_happened(
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
    live_app: FastAPI,
    clock: FakeClock,
) -> None:
    _use_notifier(live_app, RecordingNotifier([]))
    console = await _console(live_client, factory, db)
    calm = await console.start(customer_phone=PHONE)
    await console.say(calm["id"], "What time do you close today?")
    await console.say(calm["id"], "Do you have parking?")
    await console.close(calm["id"])
    angry = await console.start()
    await console.say(angry["id"], FURIOUS)
    booking = await console.client.post(
        f"/api/v1/profiles/{console.profile.id}/bookings",
        json={
            "service_id": "dine-in",
            "party_size": 2,
            "start": f"{next_tuesday(clock)}T19:00",
            "customer": {"name": "Ali Raza", "phone": PHONE},
        },
        headers=console.headers,
    )
    assert booking.status_code == 201, booking.text

    overview = await _analytics(console, days=7)
    assert overview["period_days"] == 7
    assert overview["conversations"] == {
        "total": 2,
        "closed": 1,
        "handed_off": 1,
        "needs_review": 0,
    }
    assert overview["customer_messages"] == 3
    assert overview["outcomes"] == {"answered": 2, "handoff": 1}
    knowledge = [a for a in overview["agents"] if a["agent"] == "knowledge"]
    assert sum(a["count"] for a in knowledge) == 2
    assert overview["latency"]["replies"] == 3
    assert overview["latency"]["p95_ms"] >= overview["latency"]["p50_ms"] >= 0
    assert overview["sentiment"] == {"neutral": 2, "negative": 1}
    assert overview["escalations"] == {"total": 1, "open": 1}
    assert overview["bookings"] == {"total": 1, "confirmed": 1}
    assert overview["follow_ups"] == {"total": 0}

    # Another business sees nothing of it.
    rival = await _console(live_client, factory, db, "owner@rival.example.com", "Rival")
    theirs = await live_client.get(
        f"/api/v1/profiles/{console.profile.id}/analytics", headers=rival.headers
    )
    assert theirs.status_code == 404
    assert (await _analytics(rival))["conversations"]["total"] == 0


async def test_days_must_be_sensible(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> None:
    console = await _console(live_client, factory, db)
    url = f"/api/v1/profiles/{console.profile.id}/analytics"
    for days in (0, 366):
        response = await live_client.get(url, params={"days": days}, headers=console.headers)
        assert response.status_code == 422
