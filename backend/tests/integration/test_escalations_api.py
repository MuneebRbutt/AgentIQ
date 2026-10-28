"""Sentiment on every message and escalation to a person, over the API (FR-06, TC-06).

Uses the sentiment word list (integration settings), so readings are deterministic; the
model is scripted (FakeLLM) only where a test needs the understanding step.
"""

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.dependencies import get_notifier
from app.conversations.models import SentimentRecord
from app.escalations.models import Escalation
from app.profiles.examples import load_example
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.providers.notifications import Delivery, Notice, NotificationError
from tests.fakes import FakeLLM
from tests.integration.conftest import FakeClock, OperatorFactory
from tests.integration.test_sessions_api import PHONE, Console, _console, _use_llm

pytestmark = pytest.mark.integration

FURIOUS = "This is the third time my booking has been wrong, I'm furious."
NEUTRAL = "What time do you close today?"


@pytest.fixture
async def console(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> Console:
    return await _console(live_client, factory, db)


@dataclass
class RecordingNotifier:
    notices: list[Notice]
    fail: bool = False

    async def send(self, notice: Notice) -> Delivery:
        if self.fail:
            raise NotificationError("SMS gateway down")
        self.notices.append(notice)
        return "sent"


def _use_notifier(app: FastAPI, notifier: RecordingNotifier) -> None:
    app.dependency_overrides[get_notifier] = lambda: notifier


async def _escalations(console: Console, **params: Any) -> list[dict[str, Any]]:
    response = await console.client.get(
        f"/api/v1/profiles/{console.profile.id}/escalations",
        params=params,
        headers=console.headers,
    )
    assert response.status_code == 200, response.text
    result: list[dict[str, Any]] = response.json()
    return result


async def test_tc06_a_furious_customer_is_handed_to_a_person(
    console: Console, live_app: FastAPI, clock: FakeClock, db: AsyncSession
) -> None:
    notifier = RecordingNotifier([])
    _use_notifier(live_app, notifier)
    session = await console.start(customer_phone=PHONE)

    # Step 1: classified as strongly negative.
    message = await console.say(session["id"], FURIOUS)
    sentiment = message["sentiment"]
    assert sentiment["agent"] == "sentiment"
    assert sentiment["data"]["label"] == "negative"
    assert sentiment["data"]["confidence"] >= 0.9
    assert "agent.sentiment" in message["timings"]

    # Step 2: the escalation workflow runs per the profile's rules.
    restaurant = load_example(Domain.RESTAURANT)
    assert message["outcome"] == "handoff" and message["handed_off"] is True
    assert message["reply"] == restaurant.escalation.handoff_message
    assert message["escalation_id"] is not None
    assert message["results"] == []  # nothing else was attempted

    [escalation] = await _escalations(console)
    assert escalation["id"] == message["escalation_id"]
    assert escalation["session_id"] == session["id"]
    assert escalation["reasons"] == ["negative_sentiment"]
    assert escalation["customer_message"] == FURIOUS
    assert escalation["customer_phone"] == PHONE
    assert escalation["sentiment_label"] == "negative"
    assert escalation["status"] == "open" and escalation["turn_seq"] == 1
    # The business's contact (the profile's notify_phone) was told.
    assert escalation["notification"] == "sent"
    assert escalation["notified_at"] is not None
    [notice] = notifier.notices
    assert notice.phone == restaurant.escalation.notify_phone
    assert PHONE in notice.body and "very unhappy" in notice.body

    # Post-condition: the record holds the sentiment label and escalation status.
    detail = (await console.get(f"/{session['id']}")).json()
    assert detail["status"] == "handed_off"
    assert [s["label"] for s in detail["sentiment"]] == ["negative"]
    assert detail["sentiment"][0]["reasons"] == ["negative_sentiment"]
    assert [e["id"] for e in detail["escalations"]] == [escalation["id"]]
    assert detail["turns"][1]["data"]["escalation"] == ["negative_sentiment"]
    assert (await console.send(session["id"], "Hello?")).status_code == 409


async def test_tc06_a_neutral_message_is_recorded_without_escalating(
    console: Console, db: AsyncSession
) -> None:
    session = await console.start()
    message = await console.say(session["id"], NEUTRAL)
    assert message["sentiment"]["data"]["label"] == "neutral"
    assert message["outcome"] == "answered" and message["escalation_id"] is None
    assert await _escalations(console) == []

    record = await db.scalar(select(SentimentRecord))
    assert record is not None
    assert (record.label, record.turn_seq, record.model) == ("neutral", 1, "lexicon")
    assert record.complaint is False and record.reasons == []


async def test_a_keyword_from_the_profile_escalates(console: Console) -> None:
    session = await console.start()
    message = await console.say(session["id"], "I want a refund for last night's dinner")
    assert message["outcome"] == "handoff"
    [escalation] = await _escalations(console)
    assert escalation["reasons"] == ["keyword"]


async def test_repeated_complaints_escalate_on_the_third(console: Console) -> None:
    session = await console.start()
    first = await console.say(session["id"], "The table was dirty.")
    assert first["outcome"] == "answered"
    assert first["reply"].startswith("I'm sorry about that.")  # template reply, no model
    second = await console.say(session["id"], "And the waiter was rude.")
    assert second["outcome"] == "answered"
    third = await console.say(session["id"], "My order was wrong too.")
    assert third["outcome"] == "handoff"

    [escalation] = await _escalations(console)
    assert escalation["reasons"] == ["repeated_negative"] and escalation["turn_seq"] == 5
    detail = (await console.get(f"/{session['id']}")).json()
    assert [s["turn_seq"] for s in detail["sentiment"]] == [1, 3, 5]
    assert all(s["complaint"] for s in detail["sentiment"])


async def test_an_uncertain_reading_flags_the_conversation_for_review(
    console: Console,
) -> None:
    session = await console.start()
    message = await console.say(session["id"], "The food was not good")
    data = message["sentiment"]["data"]
    assert data["label"] == "negative" and data["low_confidence"] is True
    assert message["outcome"] == "answered" and message["escalation_id"] is None

    detail = (await console.get(f"/{session['id']}")).json()
    assert detail["needs_review"] is True and detail["status"] == "open"
    assert detail["sentiment"][0]["low_confidence"] is True


async def test_a_failed_notification_still_hands_over(console: Console, live_app: FastAPI) -> None:
    _use_notifier(live_app, RecordingNotifier([], fail=True))
    session = await console.start()
    message = await console.say(session["id"], FURIOUS)
    assert message["outcome"] == "handoff"
    [escalation] = await _escalations(console)
    assert escalation["notification"] == "failed" and escalation["notified_at"] is None


async def test_without_a_contact_nobody_is_notified(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession, live_app: FastAPI
) -> None:
    notifier = RecordingNotifier([])
    _use_notifier(live_app, notifier)
    console = await _console(live_client, factory, db)
    config = load_example(Domain.RESTAURANT)
    quiet = config.model_copy(
        update={"escalation": config.escalation.model_copy(update={"notify_phone": None})}
    )
    console.profile = await ProfileService(db, factory.clock).create(
        console.profile.tenant_id, quiet
    )
    console.base = f"/api/v1/profiles/{console.profile.id}/sessions"
    session = await console.start()
    await console.say(session["id"], FURIOUS)
    [escalation] = await _escalations(console)
    assert escalation["notification"] == "none" and notifier.notices == []


async def test_an_unclear_conversation_is_escalated_when_handed_off(
    console: Console, live_app: FastAPI
) -> None:
    _use_llm(live_app, FakeLLM(understandings=[{"confidence": 0.1}] * 4))
    session = await console.start()
    for _ in range(4):
        message = await console.say(session["id"], "mmm")
    assert message["outcome"] == "handoff"
    [escalation] = await _escalations(console)
    assert escalation["reasons"] == ["unclear"] and escalation["turn_seq"] == 7


async def test_operators_acknowledge_and_resolve_escalations(
    console: Console, clock: FakeClock
) -> None:
    for text in (FURIOUS, "Get me your manager"):
        session = await console.start()
        await console.say(session["id"], text)
        clock.advance(minutes=1)
    newest, oldest = await _escalations(console)
    assert newest["reasons"] == ["keyword"] and oldest["reasons"] == ["negative_sentiment"]

    url = f"/api/v1/profiles/{console.profile.id}/escalations/{oldest['id']}"
    response = await console.client.patch(
        url, json={"status": "resolved", "note": "Called back, rebooked."}, headers=console.headers
    )
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["status"] == "resolved" and resolved["note"] == "Called back, rebooked."
    assert resolved["handled_by"] is not None

    assert [e["id"] for e in await _escalations(console, status="open")] == [newest["id"]]
    assert [e["id"] for e in await _escalations(console, status="resolved")] == [oldest["id"]]
    by_session = await _escalations(console, session_id=newest["session_id"])
    assert [e["id"] for e in by_session] == [newest["id"]]
    assert (await console.client.get(url, headers=console.headers)).json()["status"] == "resolved"

    bad = await console.client.patch(url, json={}, headers=console.headers)
    assert bad.status_code == 422
    bad = await console.client.patch(url, json={"status": "lost"}, headers=console.headers)
    assert bad.status_code == 422


async def test_another_business_cannot_see_or_handle_escalations(
    console: Console,
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
) -> None:
    session = await console.start()
    await console.say(session["id"], FURIOUS)
    [escalation] = await _escalations(console)
    rival = await _console(live_client, factory, db, "owner@rival.example.com", "Rival")

    assert await _escalations(rival) == []
    theirs = f"/api/v1/profiles/{rival.profile.id}/escalations/{escalation['id']}"
    assert (await rival.client.get(theirs, headers=rival.headers)).status_code == 404
    patched = await rival.client.patch(theirs, json={"status": "resolved"}, headers=rival.headers)
    assert patched.status_code == 404
    ours = f"/api/v1/profiles/{console.profile.id}/escalations/{escalation['id']}"
    assert (await rival.client.get(ours, headers=rival.headers)).status_code == 404

    stored = await db.get(Escalation, escalation["id"])
    assert stored is not None and stored.status == "open"
