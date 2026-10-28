"""Text messages and emails from customers against the real database (FR-02-02/03,
ADR-0011). Twilio requests are signed the way Twilio signs them; the model, the mailbox and
the mail server are fakes, so nothing is sent anywhere."""

import json
from collections.abc import AsyncIterator
from email.message import EmailMessage
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text as sql

from app.channels.email.service import EmailChannel, EmailPoller
from app.channels.inbound import InboundHandler
from app.channels.models import InboundMessage
from app.channels.sms.service import TEXT_ONLY
from app.channels.twilio import twilio_signature
from app.conversations.dependencies import build_orchestrator
from app.conversations.models import Interaction, Turn
from app.core.clock import get_clock
from app.core.config import Settings
from app.db.base import Base
from app.knowledge.dependencies import get_llm
from app.main import create_app
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.providers.notifications import NotificationError
from tests.fakes import FakeLLM
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory

pytestmark = pytest.mark.integration

BASE = "https://agentiq.test"
TOKEN = "test-auth-token"  # a test value, not a credential
NUMBER = "+14155550123"
CUSTOMER = "+923001234567"
MAILBOX = "agentiq.demo@example.com"
ADDRESS = "agentiq.demo+spice@example.com"
SENDER = "ayesha@example.org"
EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@pytest.fixture
def channel_settings(integration_settings: Settings) -> Settings:
    return integration_settings.model_copy(
        update={
            "public_base_url": BASE,
            "twilio_auth_token": SecretStr(TOKEN),
            "twilio_validate_signature": True,
            "inbound_max_messages_per_hour": 3,
            "imap_username": MAILBOX,
        }
    )


@pytest.fixture
async def app(channel_settings: Settings, clock: FakeClock) -> AsyncIterator[FastAPI]:
    app = create_app(channel_settings)
    app.dependency_overrides[get_clock] = lambda: clock
    async with app.router.lifespan_context(app):
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        async with app.state.engine.begin() as connection:
            await connection.execute(sql(f"TRUNCATE {tables} CASCADE"))
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        yield client


@pytest.fixture
async def db(app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@pytest.fixture
def factory(db: AsyncSession, channel_settings: Settings, clock: FakeClock) -> OperatorFactory:
    return OperatorFactory(db, channel_settings, clock)


class Operator:
    def __init__(self, client: httpx.AsyncClient, headers: dict[str, str], profile_id: str):
        self.client = client
        self.headers = headers
        self.profile = f"/api/v1/profiles/{profile_id}"

    async def post(self, path: str, body: dict[str, str]) -> httpx.Response:
        return await self.client.post(f"{self.profile}/{path}", json=body, headers=self.headers)

    async def get(self, path: str) -> httpx.Response:
        return await self.client.get(f"{self.profile}/{path}", headers=self.headers)

    async def delete(self, path: str) -> httpx.Response:
        return await self.client.delete(f"{self.profile}/{path}", headers=self.headers)


async def _operator(
    client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession, email: str
) -> tuple[Operator, BusinessProfile]:
    tenant = await factory.tenant(email.split("@")[1])
    await factory.operator(email, tenant=tenant)
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = await ProfileService(db, factory.clock).create(
        tenant.id, load_example(Domain.RESTAURANT)
    )
    return Operator(client, headers, str(profile.id)), profile


@pytest.fixture
async def restaurant(
    client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> tuple[Operator, BusinessProfile]:
    operator, profile = await _operator(client, factory, db, "owner@spice.example.com")
    assert (await operator.post("phone-numbers", {"number": NUMBER})).status_code == 201
    assert (await operator.post("email-addresses", {"address": ADDRESS})).status_code == 201
    return operator, profile


def _knowledge(question: str) -> dict[str, Any]:
    return {"subtasks": [{"id": "k1", "agent": "knowledge", "question": question}]}


async def _turns(db: AsyncSession, interaction: Interaction) -> list[tuple[str, str]]:
    rows = await db.scalars(
        select(Turn).where(Turn.interaction_id == interaction.id).order_by(Turn.seq)
    )
    return [(t.role, t.text) for t in rows]


async def _conversations(db: AsyncSession, channel: str) -> list[Interaction]:
    db.expire_all()
    statement = select(Interaction).where(Interaction.channel == channel)
    return list(await db.scalars(statement.order_by(Interaction.created_at)))


# --- text messages --------------------------------------------------------------------


async def _sms(
    client: httpx.AsyncClient,
    body: str,
    sid: str,
    *,
    to: str = NUMBER,
    sender: str = CUSTOMER,
    token: str = TOKEN,
) -> httpx.Response:
    params = {"MessageSid": sid, "AccountSid": "AC1", "From": sender, "To": to, "Body": body}
    params["NumMedia"] = "0"
    url = f"{BASE}/api/v1/sms/incoming"
    signature = twilio_signature(url, params, token)
    return await client.post(
        "/api/v1/sms/incoming", data=params, headers={"X-Twilio-Signature": signature}
    )


async def test_a_text_message_is_answered_and_continues_the_conversation(
    client: httpx.AsyncClient,
    app: FastAPI,
    restaurant: tuple[Operator, BusinessProfile],
    db: AsyncSession,
) -> None:
    tenant_id = restaurant[1].tenant_id
    app.dependency_overrides[get_llm] = lambda: FakeLLM(
        understandings=[_knowledge("Is the food halal?"), _knowledge("Parking?")],
        reply="Yes, **all** our food is halal & fresh.",
    )

    first = await _sms(client, "Is your food halal?", "SM0001")

    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/xml")
    assert first.text.endswith(
        "<Response><Message>Yes, all our food is halal &amp; fresh.</Message></Response>"
    )
    [conversation] = await _conversations(db, "sms")
    assert (conversation.customer_phone, conversation.status) == (CUSTOMER, "open")
    assert conversation.tenant_id == tenant_id  # from the number (NFR03-02)

    # Twilio retries a webhook it thinks failed: the message isn't answered twice.
    retried = await _sms(client, "Is your food halal?", "SM0001")
    assert retried.text == EMPTY

    second = await _sms(client, "Do you have parking?", "SM0002")
    assert "<Message>" in second.text
    assert await _conversations(db, "sms") == [conversation]  # the same conversation
    assert await _turns(db, conversation) == [
        ("customer", "Is your food halal?"),
        ("assistant", "Yes, **all** our food is halal & fresh."),
        ("customer", "Do you have parking?"),
        ("assistant", "Yes, **all** our food is halal & fresh."),
    ]


async def test_text_messages_that_get_no_answer(
    client: httpx.AsyncClient, restaurant: tuple[Operator, BusinessProfile], db: AsyncSession
) -> None:
    unknown_number = await _sms(client, "Hello?", "SM0100", to="+14155559999")
    opt_out = await _sms(client, "STOP", "SM0101")
    picture = await _sms(client, "", "SM0102")
    forged = await _sms(client, "Hello?", "SM0103", token="wrong-token")

    assert unknown_number.text == EMPTY
    assert opt_out.text == EMPTY  # Twilio handles opt-outs itself
    assert f"<Message>{TEXT_ONLY}</Message>" in picture.text
    assert forged.status_code == 403
    assert await _conversations(db, "sms") == []


async def test_a_sender_who_writes_too_often_stops_getting_answers(
    client: httpx.AsyncClient,
    app: FastAPI,
    restaurant: tuple[Operator, BusinessProfile],
    db: AsyncSession,
    clock: FakeClock,
) -> None:
    """Analysis M-15: three messages an hour here (INBOUND_MAX_MESSAGES_PER_HOUR)."""
    app.dependency_overrides[get_llm] = lambda: FakeLLM(
        understandings=[_knowledge("Hours?") for _ in range(4)], reply="We open at noon."
    )
    replies = [(await _sms(client, "Hours?", f"SM02{i}")).text for i in range(4)]

    assert ["<Message>" in reply for reply in replies] == [True, True, True, False]
    clock.advance(minutes=61)
    assert "<Message>" in (await _sms(client, "Hours?", "SM0299")).text


async def test_phone_setup_lists_the_text_message_webhook(
    restaurant: tuple[Operator, BusinessProfile],
) -> None:
    operator, _ = restaurant
    setup = (await operator.get("phone-numbers")).json()

    assert setup["sms_webhook_url"] == f"{BASE}/api/v1/sms/incoming"


# --- email ----------------------------------------------------------------------------


def _email(
    body: str,
    *,
    subject: str = "Halal food?",
    message_id: str = "<m1@example.org>",
    sender: str = f"Ayesha Khan <{SENDER}>",
    to: str = ADDRESS,
    **headers: str,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = message_id
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content(body)
    return message.as_bytes()


class FakeMailServer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[EmailMessage] = []

    async def send_message(self, message: EmailMessage) -> None:
        if self.fail:
            raise NotificationError("email server refused it (SMTPDataError)")
        self.sent.append(message)


class FakeMailbox:
    def __init__(self, messages: list[bytes], fail: bool = False) -> None:
        self.unseen = {str(uid): raw for uid, raw in enumerate(messages, start=1)}
        self.fail = fail

    def fetch_unseen(self) -> list[tuple[str, bytes]]:
        if self.fail:
            raise OSError("connection refused")
        return list(self.unseen.items())

    def mark_seen(self, uids: list[str]) -> None:
        for uid in uids:
            self.unseen.pop(uid)


def _channel(app: FastAPI, settings: Settings, clock: FakeClock, llm: FakeLLM, mail: Any) -> Any:
    orchestrator = build_orchestrator(app.state, clock, settings, llm)
    handler = InboundHandler(app.state.session_factory, clock, settings, orchestrator)
    return EmailChannel(app.state.session_factory, settings, handler, mail)


async def test_an_email_is_answered_in_the_same_thread(
    app: FastAPI,
    channel_settings: Settings,
    restaurant: tuple[Operator, BusinessProfile],
    db: AsyncSession,
    clock: FakeClock,
) -> None:
    mail = FakeMailServer()
    llm = FakeLLM(
        understandings=[_knowledge("Halal?"), _knowledge("Parking?")],
        reply="Yes, all our food is halal.",
    )
    channel = _channel(app, channel_settings, clock, llm, mail)

    outcome = await channel.process(
        _email("Hi,\nis all your food halal?\n\nThanks\n-- \nAyesha\n+92 300 1234567")
    )

    assert outcome == "answered"
    # FR-02-03: the subject and the body reach the assistant, without the signature.
    understood = json.loads(llm.json_calls[0][-1]["content"])["message"]
    assert understood == "Halal food?\n\nHi,\nis all your food halal?\n\nThanks"
    [reply] = mail.sent
    assert reply["From"] == f"Spice Garden <{ADDRESS}>"
    assert (reply["To"], reply["Subject"]) == (SENDER, "Re: Halal food?")
    assert (reply["In-Reply-To"], reply["References"]) == ("<m1@example.org>",) * 2
    assert reply["Auto-Submitted"] == "auto-replied"
    assert reply.get_content().strip() == "Yes, all our food is halal."
    [conversation] = await _conversations(db, "email")
    assert (conversation.customer_email, conversation.customer_phone) == (SENDER, None)

    # The customer replies in the thread: same conversation, quoted history cut.
    answer = _email(
        "And parking?\n\nOn Tue, 21 Oct 2026 at 09:00, Spice Garden wrote:\n> Yes, halal.",
        subject="Re: Halal food?",
        message_id="<m2@example.org>",
        In_Reply_To=reply["Message-ID"],
    )
    assert await channel.process(answer) == "answered"
    assert await channel.process(answer) == "duplicate"  # read twice, answered once
    assert await _conversations(db, "email") == [conversation]
    turns = await _turns(db, conversation)
    assert turns[2] == ("customer", "And parking?")
    assert mail.sent[1]["Subject"] == "Re: Halal food?"
    assert mail.sent[1]["References"] == f"{reply['Message-ID']} <m2@example.org>"


async def test_emails_that_are_never_answered(
    app: FastAPI,
    channel_settings: Settings,
    restaurant: tuple[Operator, BusinessProfile],
    db: AsyncSession,
    clock: FakeClock,
) -> None:
    mail = FakeMailServer()
    channel = _channel(app, channel_settings, clock, FakeLLM(), mail)

    outcomes = [
        await channel.process(_email("I'm away until Monday.", Auto_Submitted="auto-replied")),
        await channel.process(_email("Newsletter", message_id="<n@x>", List_Id="<news.x.org>")),
        await channel.process(_email("Hello", to="someone-else@example.com")),
        await channel.process(_email("Loop?", sender=ADDRESS)),
        await channel.process(_email("Loop?", sender=MAILBOX)),
        await channel.process(b"not an email"),
    ]

    assert outcomes == ["automated", "automated", "not_routed", "own", "own", "unreadable"]
    assert mail.sent == []
    assert await _conversations(db, "email") == []


async def test_a_reply_that_fails_to_send_is_reported(
    app: FastAPI,
    channel_settings: Settings,
    restaurant: tuple[Operator, BusinessProfile],
    clock: FakeClock,
) -> None:
    llm = FakeLLM(understandings=[_knowledge("Halal?")], reply="Yes.")
    channel = _channel(app, channel_settings, clock, llm, FakeMailServer(fail=True))

    assert await channel.process(_email("Halal?")) == "reply_failed"


async def test_the_poller_reads_the_mailbox_and_marks_messages_read(
    app: FastAPI,
    channel_settings: Settings,
    restaurant: tuple[Operator, BusinessProfile],
    db: AsyncSession,
    clock: FakeClock,
) -> None:
    llm = FakeLLM(understandings=[_knowledge("Halal?")], reply="Yes.")
    mail = FakeMailServer()
    mailbox = FakeMailbox([_email("Halal?"), _email("Out of office", Precedence="bulk")])
    poller = EmailPoller(mailbox, _channel(app, channel_settings, clock, llm, mail), 30)

    report = await poller.run_once()

    assert dict(report) == {"answered": 1, "automated": 1}
    assert mailbox.unseen == {}
    assert len(mail.sent) == 1
    count = await db.scalar(select(func.count()).select_from(InboundMessage))
    assert count == 1  # automated mail isn't recorded

    down = EmailPoller(FakeMailbox([], fail=True), poller._channel, 30)
    assert dict(await down.run_once()) == {}  # retried on the next pass


async def test_operators_manage_their_email_addresses(
    client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
    restaurant: tuple[Operator, BusinessProfile],
) -> None:
    operator, _ = restaurant
    other, _ = await _operator(client, factory, db, "owner@clinic.example.com")

    setup = (await operator.get("email-addresses")).json()
    assert [a["address"] for a in setup["addresses"]] == [ADDRESS]
    assert setup["inbound_enabled"] is False  # no IMAP host in the test settings
    assert setup["mailbox_address"] == MAILBOX

    taken = await other.post("email-addresses", {"address": ADDRESS.upper()})
    invalid = await operator.post("email-addresses", {"address": "not-an-address"})
    assert taken.status_code == 409
    assert invalid.status_code == 422
    assert invalid.json()["field"] == "address"

    address_id = setup["addresses"][0]["id"]
    assert (await other.delete(f"email-addresses/{address_id}")).status_code == 404
    assert (await operator.delete(f"email-addresses/{address_id}")).status_code == 204
    assert (await operator.get("email-addresses")).json()["addresses"] == []
