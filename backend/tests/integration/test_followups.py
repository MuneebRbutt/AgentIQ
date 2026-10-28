"""Follow-ups after bookings and conversations, end to end (FR-07, UC-07, TC-07).

Bookings and conversations go through the API; the worker is run one pass at a time with
the fake clock, so "advance the system clock to the reminder window" is a line of code.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from app import cli
from app.core.config import Settings
from app.main import create_app
from app.providers.notifications import Delivery, Notice
from app.workers.followups import FollowUpWorker
from tests.integration.conftest import (
    DEFAULT_PASSWORD,
    FakeClock,
    OperatorFactory,
    next_tuesday,
)
from tests.integration.test_escalations_api import FURIOUS, RecordingNotifier, _use_notifier
from tests.integration.test_sessions_api import PHONE, Console, _console

pytestmark = pytest.mark.integration

LAHORE = ZoneInfo("Asia/Karachi")


class Desk:
    """The restaurant's operator: bookings, conversations and the follow-up list."""

    def __init__(self, console: Console, clock: FakeClock) -> None:
        self.console = console
        self.client = console.client
        self.headers = console.headers
        self.base = f"/api/v1/profiles/{console.profile.id}"
        self.day = next_tuesday(clock)

    async def book(self, **overrides: Any) -> dict[str, Any]:
        body = {
            "service_id": "dine-in",
            "party_size": 4,
            "start": f"{self.day}T19:00",
            "customer": {"name": "Ali Raza", "phone": PHONE},
            **overrides,
        }
        response = await self.client.post(f"{self.base}/bookings", json=body, headers=self.headers)
        assert response.status_code == 201, response.text
        booking: dict[str, Any] = response.json()
        return booking

    async def relogin(self) -> None:
        """After moving the clock by days, the morning's login has expired."""
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "owner@spice.example.com", "password": DEFAULT_PASSWORD},
        )
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        self.console.headers = self.headers

    async def follow_ups(self, **params: Any) -> list[dict[str, Any]]:
        response = await self.client.get(
            f"{self.base}/follow-ups", params=params, headers=self.headers
        )
        assert response.status_code == 200, response.text
        result: list[dict[str, Any]] = response.json()
        return result


@pytest.fixture
async def desk(
    live_client: httpx.AsyncClient, factory: OperatorFactory, clock: FakeClock, db: Any
) -> Desk:
    return Desk(await _console(live_client, factory, db), clock)


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier([])


@pytest.fixture
def worker(
    live_app: FastAPI,
    clock: FakeClock,
    integration_settings: Settings,
    notifier: RecordingNotifier,
) -> FollowUpWorker:
    return FollowUpWorker(live_app.state.session_factory, clock, integration_settings, notifier)


def _at(day: Any, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=LAHORE)


async def test_tc07_confirmation_reminder_and_feedback_are_sent_without_anyone(
    desk: Desk, worker: FollowUpWorker, notifier: RecordingNotifier, clock: FakeClock
) -> None:
    # Pre-condition: a booking was created in a prior interaction.
    booking = await desk.book()

    # Step 1: the confirmation goes out automatically (FR-07-02).
    report = await worker.run_once()
    assert report.booking_events == 1 and report.queued == 2
    [confirmation] = notifier.notices
    assert confirmation.phone == PHONE and confirmation.email is None
    assert booking["reference"] in confirmation.body
    assert "is confirmed for Table reservation" in confirmation.body
    assert "7:00 PM, party of 4" in confirmation.body
    reminder = (await desk.follow_ups(kind="reminder"))[0]
    assert reminder["status"] == "pending" and reminder["booking_id"] == booking["id"]
    starts = datetime.fromisoformat(booking["starts_at"])
    assert datetime.fromisoformat(reminder["due_at"]) == starts - timedelta(hours=24)

    # Nothing more until the reminder is due.
    clock.current = starts - timedelta(hours=25)
    await worker.run_once()
    assert len(notifier.notices) == 1

    # Step 2: advance the clock to the reminder window (FR-07-03).
    clock.current = starts - timedelta(hours=24)
    await worker.run_once()
    assert len(notifier.notices) == 2
    assert notifier.notices[1].body.startswith("Reminder from Spice Garden")

    # Step 3: a conversation completes with feedback enabled (FR-07-04).
    await desk.relogin()
    session = await desk.console.start(customer_phone=PHONE)
    await desk.console.say(session["id"], "What time do you close today?")
    assert (await desk.console.close(session["id"])).status_code == 200
    await worker.run_once()
    [feedback] = await desk.follow_ups(kind="feedback")
    assert feedback["status"] == "pending" and feedback["session_id"] == session["id"]
    clock.advance(minutes=60)  # the restaurant asks an hour later
    await worker.run_once()
    assert notifier.notices[2].body.startswith("Thank you for contacting Spice Garden")

    # Post-condition: every follow-up is logged as sent.
    await desk.relogin()
    logged = await desk.follow_ups()
    assert sorted(f["kind"] for f in logged) == ["confirmation", "feedback", "reminder"]
    assert {f["status"] for f in logged} == {"sent"}
    assert all(f["sent_at"] and f["attempts"] == 1 for f in logged)

    # A second pass finds nothing new to do.
    again = await worker.run_once()
    assert (again.booking_events, again.conversations, again.delivered) == (0, 0, {})


async def test_a_changed_booking_gets_a_new_confirmation_and_moved_reminder(
    desk: Desk, worker: FollowUpWorker, notifier: RecordingNotifier
) -> None:
    booking = await desk.book()
    await worker.run_once()
    response = await desk.client.patch(
        f"{desk.base}/bookings/{booking['id']}",
        json={"start": f"{desk.day}T20:30", "party_size": 6},
        headers=desk.headers,
    )
    assert response.status_code == 200, response.text
    await worker.run_once()

    assert "has been changed" in notifier.notices[1].body
    assert "8:30 PM, party of 6" in notifier.notices[1].body
    reminders = await desk.follow_ups(kind="reminder", booking_id=booking["id"])
    by_status = {r["status"]: r for r in reminders}
    assert set(by_status) == {"pending", "cancelled"}
    moved = _at(desk.day, 20) + timedelta(minutes=30) - timedelta(hours=24)
    assert datetime.fromisoformat(by_status["pending"]["due_at"]) == moved


async def test_a_cancelled_booking_gets_no_reminder(
    desk: Desk, worker: FollowUpWorker, notifier: RecordingNotifier, clock: FakeClock
) -> None:
    booking = await desk.book()
    await worker.run_once()
    url = f"{desk.base}/bookings/{booking['id']}/cancel"
    assert (await desk.client.post(url, headers=desk.headers)).status_code == 200
    await worker.run_once()

    [reminder] = await desk.follow_ups(kind="reminder")
    assert reminder["status"] == "cancelled"
    clock.current = datetime.fromisoformat(booking["starts_at"]) - timedelta(hours=1)
    await worker.run_once()
    assert len(notifier.notices) == 1  # only the original confirmation


async def test_a_failed_message_is_logged_and_retried_once(
    desk: Desk,
    worker: FollowUpWorker,
    notifier: RecordingNotifier,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    notifier.fail = True
    await desk.book(start=f"{desk.day}T12:00")
    await worker.run_once()
    [confirmation] = await desk.follow_ups(kind="confirmation")
    assert confirmation["status"] == "pending" and confirmation["attempts"] == 1
    assert confirmation["last_error"] == "SMS gateway down"

    # Not retried before the retry delay.
    clock.advance(minutes=integration_settings.followup_retry_minutes - 1)
    await worker.run_once()
    assert (await desk.follow_ups(kind="confirmation"))[0]["attempts"] == 1

    clock.advance(minutes=1)
    await worker.run_once()
    [confirmation] = await desk.follow_ups(kind="confirmation")
    assert confirmation["status"] == "failed" and confirmation["attempts"] == 2
    assert notifier.notices == []

    # A second message whose retry succeeds ends up sent.
    await desk.book(start=f"{desk.day}T13:00", customer={"name": "Sana", "phone": "0321 7654321"})
    await worker.run_once()
    notifier.fail = False
    clock.advance(minutes=integration_settings.followup_retry_minutes)
    await worker.run_once()
    statuses = {f["recipient"]: f["status"] for f in await desk.follow_ups(kind="confirmation")}
    assert statuses == {PHONE: "failed", "0321 7654321": "sent"}


async def test_no_feedback_after_a_hand_off_or_without_a_number(
    desk: Desk, worker: FollowUpWorker, live_app: FastAPI, clock: FakeClock
) -> None:
    _use_notifier(live_app, RecordingNotifier([]))
    angry = await desk.console.start(customer_phone=PHONE)
    assert (await desk.console.say(angry["id"], FURIOUS))["outcome"] == "handoff"
    anonymous = await desk.console.start()
    await desk.console.say(anonymous["id"], "What time do you close today?")
    await desk.console.close(anonymous["id"])

    report = await worker.run_once()
    assert report.conversations == 2 and report.queued == 0
    assert await desk.follow_ups(kind="feedback") == []


async def test_a_quiet_conversation_counts_as_ended(
    desk: Desk,
    worker: FollowUpWorker,
    notifier: RecordingNotifier,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    session = await desk.console.start(customer_phone=PHONE)
    await desk.console.say(session["id"], "Do you have parking?")
    clock.advance(minutes=integration_settings.conversation_idle_minutes - 1)
    assert (await worker.run_once()).closed_idle == 0

    clock.advance(minutes=1)
    report = await worker.run_once()
    assert report.closed_idle == 1 and report.queued == 1
    detail = (await desk.console.get(f"/{session['id']}")).json()
    assert detail["status"] == "closed"
    # Feedback is due an hour after the last message, not after the idle check.
    [feedback] = await desk.follow_ups(kind="feedback")
    last_message = clock.current - timedelta(minutes=integration_settings.conversation_idle_minutes)
    assert datetime.fromisoformat(feedback["due_at"]) == last_message + timedelta(minutes=60)


async def test_a_failed_escalation_notice_is_retried_once(
    desk: Desk,
    worker: FollowUpWorker,
    notifier: RecordingNotifier,
    live_app: FastAPI,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    _use_notifier(live_app, RecordingNotifier([], fail=True))
    session = await desk.console.start(customer_phone=PHONE)
    await desk.console.say(session["id"], FURIOUS)
    escalations_url = f"{desk.base}/escalations"
    [escalation] = (await desk.client.get(escalations_url, headers=desk.headers)).json()
    assert escalation["notification"] == "failed"
    [retry] = await desk.follow_ups(kind="escalation")
    assert retry["status"] == "pending" and retry["attempts"] == 1
    assert retry["escalation_id"] == escalation["id"]

    clock.advance(minutes=integration_settings.followup_retry_minutes)
    await worker.run_once()
    [notice] = notifier.notices
    assert "needs a call back" in notice.subject and PHONE in notice.body
    [escalation] = (await desk.client.get(escalations_url, headers=desk.headers)).json()
    assert escalation["notification"] == "sent" and escalation["notified_at"] is not None
    assert (await desk.follow_ups(kind="escalation"))[0]["status"] == "sent"


async def test_operators_see_and_cancel_follow_ups(
    desk: Desk,
    worker: FollowUpWorker,
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: Any,
) -> None:
    booking = await desk.book()
    await worker.run_once()
    reminder, confirmation = await desk.follow_ups()  # latest due first
    assert (reminder["kind"], confirmation["kind"]) == ("reminder", "confirmation")
    assert [f["id"] for f in await desk.follow_ups(status="sent")] == [confirmation["id"]]
    assert await desk.follow_ups(booking_id=booking["id"], kind="feedback") == []

    url = f"{desk.base}/follow-ups/{reminder['id']}"
    detail = (await desk.client.get(url, headers=desk.headers)).json()
    assert detail["body"] == reminder["body"] and detail["channel"] == "sms"
    cancelled = await desk.client.post(f"{url}/cancel", headers=desk.headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    sent_url = f"{desk.base}/follow-ups/{confirmation['id']}/cancel"
    refused = await desk.client.post(sent_url, headers=desk.headers)
    assert refused.status_code == 409

    # Another business sees none of it.
    rival = await _console(live_client, factory, db, "owner@rival.example.com", "Rival")
    rival_base = f"/api/v1/profiles/{rival.profile.id}/follow-ups"
    listed = await rival.client.get(rival_base, headers=rival.headers)
    assert listed.json() == []
    theirs = f"{rival_base}/{reminder['id']}"
    assert (await rival.client.get(theirs, headers=rival.headers)).status_code == 404
    assert (await rival.client.post(f"{theirs}/cancel", headers=rival.headers)).status_code == 404
    assert (await rival.client.get(url, headers=rival.headers)).status_code == 404


async def test_the_background_loop_runs_until_stopped(
    desk: Desk,
    live_app: FastAPI,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    delivered = asyncio.Event()
    notices: list[Notice] = []

    class Signalling:
        async def send(self, notice: Notice) -> Delivery:
            notices.append(notice)
            delivered.set()
            return "sent"

    worker = FollowUpWorker(
        live_app.state.session_factory, clock, integration_settings, Signalling()
    )
    await desk.book()
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.run_forever(stop))
    await asyncio.wait_for(delivered.wait(), 10)  # the first pass starts at once
    stop.set()
    await asyncio.wait_for(loop, 5)
    assert "is confirmed" in notices[0].body


async def test_run_followups_command_does_one_pass(
    desk: Desk, integration_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    await desk.book()
    await cli.run_followups(integration_settings, once=True)
    out = capsys.readouterr().out
    assert "handled 1 booking event(s)" in out and "queued 2" in out
    # No SMS provider is configured in tests, so the confirmation is only logged.
    assert "{'logged': 1}" in out
    [confirmation] = await desk.follow_ups(kind="confirmation")
    assert confirmation["status"] == "logged"


async def test_the_api_runs_the_worker_and_stops_it_on_shutdown(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings.model_copy(update={"followup_worker_enabled": True}))
    async with asyncio.timeout(10), app.router.lifespan_context(app):
        await asyncio.sleep(0.2)  # let the first pass run
