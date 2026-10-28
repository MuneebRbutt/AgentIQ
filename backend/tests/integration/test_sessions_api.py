"""Conversations over the API: the text test console (FR-08-02), logs (FR-08-03), per-stage
timings (FR-10-02) and isolation between businesses (NFR03-02).

The model is scripted (FakeLLM) through the same dependency the API uses, so these tests
cover the whole path from HTTP request to stored turns without a paid API.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.models import Booking
from app.knowledge.dependencies import get_llm
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.providers.llm import LLMClient
from tests.fakes import FakeLLM
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory, next_tuesday

pytestmark = pytest.mark.integration

PHONE = "+92 300 1234567"


@dataclass
class Console:
    """One operator using the test console of their restaurant profile."""

    client: httpx.AsyncClient
    headers: dict[str, str]
    profile: BusinessProfile
    base: str = field(init=False)

    def __post_init__(self) -> None:
        self.base = f"/api/v1/profiles/{self.profile.id}/sessions"

    async def start(self, **body: Any) -> dict[str, Any]:
        response = await self.client.post(self.base, json=body, headers=self.headers)
        assert response.status_code == 201, response.text
        session: dict[str, Any] = response.json()
        return session

    async def send(self, session_id: str, text: str) -> httpx.Response:
        return await self.client.post(
            f"{self.base}/{session_id}/messages", json={"text": text}, headers=self.headers
        )

    async def say(self, session_id: str, text: str) -> dict[str, Any]:
        response = await self.send(session_id, text)
        assert response.status_code == 200, response.text
        message: dict[str, Any] = response.json()
        return message

    async def get(self, path: str = "", **params: Any) -> httpx.Response:
        return await self.client.get(f"{self.base}{path}", params=params, headers=self.headers)

    async def close(self, session_id: str) -> httpx.Response:
        return await self.client.post(f"{self.base}/{session_id}/close", headers=self.headers)


async def _console(
    client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
    email: str = "owner@spice.example.com",
    business: str = "Spice Garden",
) -> Console:
    tenant = await factory.tenant(business)
    await factory.operator(email, tenant=tenant)
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    profile = await ProfileService(db, factory.clock).create(
        tenant.id, load_example(Domain.RESTAURANT)
    )
    return Console(client, headers, profile)


@pytest.fixture
async def console(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> Console:
    return await _console(live_client, factory, db)


def _use_llm(app: FastAPI, llm: LLMClient) -> None:
    app.dependency_overrides[get_llm] = lambda: llm


def _book(**fields: Any) -> dict[str, Any]:
    return {"id": "b1", "agent": "booking", "booking": {"action": "create", **fields}}


async def test_tc03_text_conversation_through_the_api(
    console: Console, live_app: FastAPI, clock: FakeClock, db: AsyncSession
) -> None:
    day = next_tuesday(clock)
    _use_llm(
        live_app,
        FakeLLM(
            reply="Here you go.",
            understandings=[
                {
                    "subtasks": [
                        {"id": "k1", "agent": "knowledge", "question": "Opening hours?"},
                        _book(service_id="dine-in", party_size=4, date=str(day), time="19:30"),
                    ]
                },
                {"subtasks": [_book(customer_name="Ali Raza")]},
                {"confirmation": "yes"},
            ],
        ),
    )
    session = await console.start(customer_phone=PHONE)
    assert session["status"] == "open" and session["turn_count"] == 0
    assert session["customer_phone"] == PHONE
    assert session["greeting"].startswith("Assalam-o-Alaikum, welcome to Spice Garden!")

    first = await console.say(
        session["id"], "What are your opening hours? And a table for 4 on Tuesday at 7:30pm."
    )
    assert first["session_id"] == session["id"]
    assert first["reply"] == "Here you go." and first["outcome"] == "answered"
    assert {r["agent"]: r["status"] for r in first["results"]} == {
        "knowledge": "completed",
        "booking": "needs_info",  # only the name is missing: the phone came with the session
    }
    # Per-stage timings in milliseconds (FR-10-02).
    timings = first["timings"]
    assert {"understand", "reply", "total", "agent.knowledge.k1", "agent.booking.b1"} <= set(
        timings
    )
    assert all(value >= 0 for value in timings.values())
    assert timings["total"] >= timings["understand"]

    second = await console.say(session["id"], "Ali Raza")
    assert [r["status"] for r in second["results"]] == ["needs_confirmation"]
    assert await db.scalar(select(func.count()).select_from(Booking)) == 0  # FR-04-03
    third = await console.say(session["id"], "Yes")
    assert [r["status"] for r in third["results"]] == ["completed"]
    assert await db.scalar(select(func.count()).select_from(Booking)) == 1

    # The log shows every turn, with what each agent did and how long it took (FR-08-03).
    detail = (await console.get(f"/{session['id']}")).json()
    assert detail["turn_count"] == 6
    assert [t["role"] for t in detail["turns"]] == ["customer", "assistant"] * 3
    assert detail["turns"][0]["text"].startswith("What are your opening hours?")
    assert detail["turns"][0]["data"] == {}
    answer = detail["turns"][1]["data"]
    assert answer["outcome"] == "answered" and "total" in answer["timings"]
    assert [r["agent"] for r in answer["results"]] == ["knowledge", "booking"]

    [listed] = (await console.get()).json()
    assert listed["id"] == session["id"] and listed["turn_count"] == 6


async def test_without_a_model_the_console_still_answers(console: Console) -> None:
    session = await console.start()
    message = await console.say(session["id"], "Is there a kids' menu?")
    assert message["outcome"] == "answered"
    assert "kids' menu and high chairs" in message["reply"]
    assert [r["status"] for r in message["results"]] == ["completed"]
    assert {"understand", "reply", "total"} <= set(message["timings"])


async def test_closed_conversations_take_no_more_messages(console: Console) -> None:
    session = await console.start()
    await console.say(session["id"], "Is there a kids' menu?")
    response = await console.close(session["id"])
    assert response.status_code == 200 and response.json()["status"] == "closed"
    assert (await console.close(session["id"])).status_code == 200  # closing twice is fine

    response = await console.send(session["id"], "One more thing")
    assert response.status_code == 409
    assert response.json()["detail"] == "This conversation has ended. Start a new one."
    assert (await console.get(f"/{session['id']}")).json()["turn_count"] == 2


async def test_handed_off_conversations_take_no_more_messages(
    console: Console, live_app: FastAPI
) -> None:
    _use_llm(live_app, FakeLLM(understandings=[{"confidence": 0.1}] * 4))
    session = await console.start()
    outcomes = [(await console.say(session["id"], "mmm"))["outcome"] for _ in range(4)]
    assert outcomes == ["clarify", "clarify", "clarify", "handoff"]

    response = await console.send(session["id"], "hello?")
    assert response.status_code == 409
    assert "handed over to a person" in response.json()["detail"]
    handed_off = (await console.get(status="handed_off")).json()
    assert [s["id"] for s in handed_off] == [session["id"]]


async def test_listing_filters_and_pages_most_recent_first(
    console: Console, clock: FakeClock
) -> None:
    first = await console.start()
    clock.advance(minutes=1)
    second = await console.start()
    clock.advance(minutes=1)
    await console.close(first["id"])  # closing counts as activity

    ids = [s["id"] for s in (await console.get()).json()]
    assert ids == [first["id"], second["id"]]
    assert [s["id"] for s in (await console.get(status="open")).json()] == [second["id"]]
    assert [s["id"] for s in (await console.get(limit=1, offset=1)).json()] == [second["id"]]
    assert (await console.get(limit=0)).status_code == 422
    assert (await console.get(status="lost")).status_code == 422


async def test_bad_requests(console: Console) -> None:
    session = await console.start()
    assert (await console.send(session["id"], "   ")).status_code == 422
    assert (await console.send(session["id"], "x" * 2001)).status_code == 422
    assert (await console.send(str(uuid.uuid4()), "hello")).status_code == 404
    assert (await console.get(f"/{uuid.uuid4()}")).status_code == 404
    response = await console.client.post(
        console.base,
        json={"customer_phone": PHONE, "tenant_id": str(uuid.uuid4())},
        headers=console.headers,
    )
    assert response.status_code == 422  # the tenant always comes from the login

    console.headers = {}
    assert (await console.get()).status_code == 401
    assert (await console.send(session["id"], "hello")).status_code == 401


async def test_another_business_cannot_see_or_continue_a_conversation(
    console: Console,
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
) -> None:
    session = await console.start()
    await console.say(session["id"], "Is there a kids' menu?")
    rival = await _console(live_client, factory, db, "owner@rival.example.com", "Rival")

    # Through their own profile, our conversation doesn't exist...
    assert (await rival.send(session["id"], "hello")).status_code == 404
    assert (await rival.get(f"/{session['id']}")).status_code == 404
    assert (await rival.close(session["id"])).status_code == 404
    assert (await rival.get()).json() == []
    # ...and our profile doesn't exist for them either.
    rival.base = console.base
    assert (await rival.get()).status_code == 404
    assert (await rival.send(session["id"], "hello")).status_code == 404
    assert (await rival.client.post(rival.base, json={}, headers=rival.headers)).status_code == 404

    detail = (await console.get(f"/{session['id']}")).json()
    assert detail["turn_count"] == 2 and detail["status"] == "open"
