"""Phone calls through Twilio's webhooks against the real database (TC-02; FR-02-01/04/05/06,
ADR-0010). Requests are signed the way Twilio signs them; the model, Whisper and the voice
are fakes, so no paid API is called."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text as sql

from app.channels.twilio import twilio_signature
from app.channels.voice.models import PhoneNumber, VoiceCall
from app.channels.voice.service import (
    DIDNT_CATCH,
    GOODBYE,
    NOT_CONNECTED,
    STILL_THERE,
    TROUBLE,
    TwilioRecordings,
)
from app.conversations.dependencies import get_orchestrator
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
from app.providers.speech import SpeechError
from tests.fakes import FakeLLM
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory, next_tuesday

pytestmark = pytest.mark.integration

BASE = "https://voice.test"
TOKEN = "test-auth-token"  # a test value, not a credential
NUMBER = "+14155550123"
CALLER = "+923001234567"
RECORDING = "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1"


@pytest.fixture
def voice_settings(integration_settings: Settings) -> Settings:
    return integration_settings.model_copy(
        update={
            "public_base_url": BASE,
            "twilio_auth_token": SecretStr(TOKEN),
            "twilio_validate_signature": True,
            "voice_max_silences": 1,
        }
    )


@pytest.fixture
async def voice_app(voice_settings: Settings, clock: FakeClock) -> AsyncIterator[FastAPI]:
    app = create_app(voice_settings)
    app.dependency_overrides[get_clock] = lambda: clock
    async with app.router.lifespan_context(app):
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        async with app.state.engine.begin() as connection:
            await connection.execute(sql(f"TRUNCATE {tables} CASCADE"))
        yield app


@pytest.fixture
async def client(voice_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=voice_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        yield client


@pytest.fixture
async def db(voice_app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with voice_app.state.session_factory() as session:
        yield session


@pytest.fixture
def factory(db: AsyncSession, voice_settings: Settings, clock: FakeClock) -> OperatorFactory:
    return OperatorFactory(db, voice_settings, clock)


class Operator:
    def __init__(self, client: httpx.AsyncClient, headers: dict[str, str], profile_id: str):
        self.client = client
        self.headers = headers
        self.base = f"/api/v1/profiles/{profile_id}/phone-numbers"

    async def add(self, number: str) -> httpx.Response:
        return await self.client.post(self.base, json={"number": number}, headers=self.headers)

    async def setup(self) -> dict[str, Any]:
        response = await self.client.get(self.base, headers=self.headers)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body


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
) -> BusinessProfile:
    operator, profile = await _operator(client, factory, db, "owner@spice.example.com")
    assert (await operator.add(NUMBER)).status_code == 201
    return profile


class Call:
    """One phone call, as Twilio would make the webhook requests."""

    def __init__(self, client: httpx.AsyncClient, sid: str = "CA0001") -> None:
        self.client = client
        self.sid = sid

    async def post(self, path: str, token: str = TOKEN, **fields: str) -> httpx.Response:
        params = {"CallSid": self.sid, "AccountSid": "AC1", **fields}
        url = f"{BASE}/api/v1/voice/{path}"
        signature = twilio_signature(url, params, token)
        return await self.client.post(
            f"/api/v1/voice/{path}", data=params, headers={"X-Twilio-Signature": signature}
        )

    async def twiml(self, path: str, **fields: str) -> str:
        response = await self.post(path, **fields)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/xml")
        return response.text

    async def start(self, to: str = NUMBER) -> str:
        return await self.twiml("incoming", From=CALLER, To=to, CallStatus="ringing")

    async def say(self, words: str) -> str:
        return await self.twiml("turn", SpeechResult=words, Confidence="0.92")


class FakeSpeech:
    name = "fake-voice"
    content_type = "audio/mpeg"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        if self.fail:
            raise SpeechError("tts unreachable: ConnectError")
        self.spoken.append(text)
        return b"ID3" + text.encode()

    async def aclose(self) -> None:
        return None


class FakeWhisper:
    name = "fake-whisper"

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.heard: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        if self.text is None:
            raise SpeechError("stt returned HTTP 503")
        self.heard.append((audio, content_type))
        return self.text

    async def aclose(self) -> None:
        return None


def _use_whisper(app: FastAPI, whisper: FakeWhisper) -> None:
    app.state.speech_to_text = whisper
    app.state.twilio_recordings = TwilioRecordings(
        "AC1", TOKEN, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"RIFF"))
    )


async def _call(db: AsyncSession, sid: str = "CA0001") -> VoiceCall:
    db.expire_all()
    call = await db.scalar(select(VoiceCall).where(VoiceCall.call_sid == sid))
    assert call is not None
    return call


def _book(**fields: Any) -> dict[str, Any]:
    return {"id": "b1", "agent": "booking", "booking": {"action": "create", **fields}}


async def test_tc02_a_call_is_answered_transcribed_and_spoken(
    client: httpx.AsyncClient,
    voice_app: FastAPI,
    restaurant: BusinessProfile,
    db: AsyncSession,
    clock: FakeClock,
) -> None:
    tenant_id = restaurant.tenant_id
    tomorrow = next_tuesday(clock)
    voice_app.dependency_overrides[get_llm] = lambda: FakeLLM(
        understandings=[
            {
                "subtasks": [
                    _book(service_id="dine-in", party_size=2, date=str(tomorrow), time="19:00")
                ]
            }
        ],
        reply="May I have your name for the booking?",
    )
    tts = FakeSpeech()
    voice_app.state.text_to_speech = tts
    call = Call(client)

    # Step 1: the call is answered and a voice conversation opens (FR-02-01).
    greeting = await call.start()
    assert f'<Gather input="speech" action="{BASE}/api/v1/voice/turn"' in greeting
    assert f"<Play>{BASE}/api/v1/voice/audio/" in greeting
    assert greeting.endswith(
        f'<Redirect method="POST">{BASE}/api/v1/voice/silence</Redirect></Response>'
    )
    assert tts.spoken[0].startswith("Assalam-o-Alaikum, welcome to Spice Garden!")
    voice_call = await _call(db)
    interaction = await db.get(Interaction, voice_call.interaction_id)
    assert interaction is not None
    assert (interaction.channel, interaction.customer_phone, interaction.status) == (
        "voice",
        CALLER,
        "open",
    )
    assert interaction.tenant_id == tenant_id  # from the dialled number (NFR03-02)

    # Step 2: what the caller said reaches the orchestrator as text (FR-02-04).
    reply = await call.say("I'd like to book a table for two tomorrow at 7pm")
    turns = list(
        await db.scalars(
            select(Turn).where(Turn.interaction_id == interaction.id).order_by(Turn.seq)
        )
    )
    assert [(t.role, t.text) for t in turns] == [
        ("customer", "I'd like to book a table for two tomorrow at 7pm"),
        ("assistant", "May I have your name for the booking?"),
    ]

    # Step 3: the reply is spoken back with the configured voice (FR-02-05).
    assert tts.spoken[-1] == "May I have your name for the booking?"
    token = reply.split("/api/v1/voice/audio/")[1].split("<")[0]
    audio = await client.get(f"/api/v1/voice/audio/{token}")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    assert audio.content == b"ID3May I have your name for the booking?"

    # Per-stage latency is recorded on the call and on the turn (FR-02-06).
    [timing] = (await _call(db)).timings
    assert timing["stt"] == "twilio" and timing["tts"] == "fake-voice"
    assert timing["stt_ms"] is None
    assert timing["orchestrator_ms"] > 0 and timing["tts_ms"] >= 0
    assert timing["server_ms"] >= timing["orchestrator_ms"]
    await db.refresh(turns[1])
    assert {"understand", "total", "tts"} <= set(turns[1].data["timings"])

    # Hanging up ends the call and the conversation (so feedback can follow).
    ended = await call.post("status", CallStatus="completed", CallDuration="42")
    assert ended.status_code == 204
    voice_call = await _call(db)
    assert (voice_call.status, voice_call.end_reason) == ("ended", "completed")
    await db.refresh(interaction)
    assert interaction.status == "closed"


async def test_whisper_transcribes_the_recording(
    client: httpx.AsyncClient, voice_app: FastAPI, restaurant: BusinessProfile, db: AsyncSession
) -> None:
    voice_app.dependency_overrides[get_llm] = lambda: FakeLLM(
        understandings=[{"subtasks": [{"id": "k1", "agent": "knowledge", "question": "Halal?"}]}],
        reply="Yes, all our food is halal.",
    )
    whisper = FakeWhisper("Is your food halal?")
    _use_whisper(voice_app, whisper)
    call = Call(client)

    greeting = await call.start()
    assert f'<Record action="{BASE}/api/v1/voice/turn"' in greeting
    assert "<Say " in greeting  # Twilio's voice: no TTS configured

    reply = await call.twiml("turn", RecordingUrl=RECORDING, RecordingDuration="3")

    assert whisper.heard == [(b"RIFF", "audio/wav")]
    assert "Yes, all our food is halal." in reply and "<Record " in reply
    [timing] = (await _call(db)).timings
    assert timing["stt"] == "fake-whisper" and timing["stt_ms"] >= 0


async def test_speech_failures_fall_back_instead_of_dropping_the_call(
    client: httpx.AsyncClient, voice_app: FastAPI, restaurant: BusinessProfile
) -> None:
    """UC-02 exception: a speech service that fails never leaves the caller in silence."""
    voice_app.state.text_to_speech = FakeSpeech(fail=True)
    _use_whisper(voice_app, FakeWhisper(None))
    call = Call(client)

    greeting = await call.start()
    assert "<Say " in greeting and "Spice Garden" in greeting  # spoken by Twilio instead

    reply = await call.twiml("turn", RecordingUrl=RECORDING, RecordingDuration="2")
    assert DIDNT_CATCH in reply and "<Record " in reply


async def test_silence_is_asked_about_then_the_call_ends(
    client: httpx.AsyncClient, restaurant: BusinessProfile, db: AsyncSession
) -> None:
    call = Call(client)
    await call.start()

    first = await call.twiml("silence")
    second = await call.twiml("silence")

    assert STILL_THERE in first and "<Gather " in first
    assert GOODBYE in second and second.endswith("<Hangup/></Response>")
    assert (await _call(db)).silences == 2


async def test_a_complaint_hands_the_call_to_a_person(
    client: httpx.AsyncClient, voice_app: FastAPI, restaurant: BusinessProfile, db: AsyncSession
) -> None:
    voice_app.dependency_overrides[get_llm] = lambda: FakeLLM(understandings=[{"subtasks": []}])
    call = Call(client)
    await call.start()

    reply = await call.say("I want a refund, the food was terrible and I'm furious")

    assert reply.endswith("<Hangup/></Response>")
    interaction = await db.get(Interaction, (await _call(db)).interaction_id)
    assert interaction is not None and interaction.status == "handed_off"


async def test_unknown_numbers_and_bad_signatures_are_refused(
    client: httpx.AsyncClient, restaurant: BusinessProfile, db: AsyncSession
) -> None:
    call = Call(client, sid="CA0002")

    not_connected = await call.start(to="+14155559999")
    forged = await call.post("incoming", token="wrong-token", From=CALLER, To=NUMBER)
    unsigned = await client.post("/api/v1/voice/incoming", data={"CallSid": "CA3", "To": NUMBER})

    assert NOT_CONNECTED in not_connected and not_connected.endswith("<Hangup/></Response>")
    assert forged.status_code == 403 and unsigned.status_code == 403
    assert list(await db.scalars(select(Interaction))) == []


async def test_a_failing_turn_still_answers_with_an_apology(
    client: httpx.AsyncClient, voice_app: FastAPI, restaurant: BusinessProfile
) -> None:
    class Broken:
        async def handle(self, request: object) -> None:
            raise RuntimeError("database fell over")

    call = Call(client)
    await call.start()
    voice_app.dependency_overrides[get_orchestrator] = Broken

    reply = await call.say("Hello?")

    assert TROUBLE in reply and reply.endswith("<Hangup/></Response>")


async def test_twilio_retrying_the_first_webhook_reuses_the_call(
    client: httpx.AsyncClient, restaurant: BusinessProfile, db: AsyncSession
) -> None:
    call = Call(client)
    await call.start()
    again = await call.start()

    assert "Spice Garden" in again
    assert len(list(await db.scalars(select(VoiceCall)))) == 1


async def test_operators_manage_their_numbers(
    client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> None:
    operator, profile = await _operator(client, factory, db, "owner@spice.example.com")
    rival, _ = await _operator(client, factory, db, "owner@rival.example.com")

    added = await operator.add("+1 (415) 555-0123")
    assert added.status_code == 201 and added.json()["number"] == NUMBER
    assert (await rival.add(NUMBER)).status_code == 409  # one business per number
    assert (await operator.add("12")).status_code == 422

    setup = await operator.setup()
    assert [n["number"] for n in setup["numbers"]] == [NUMBER]
    assert setup["incoming_webhook_url"] == f"{BASE}/api/v1/voice/incoming"
    assert setup["status_callback_url"] == f"{BASE}/api/v1/voice/status"
    assert setup["signatures_checked"] is True

    number_id = added.json()["id"]
    other_tenant = await client.delete(
        f"{rival.base.rsplit('/', 2)[0]}/{profile.id}/phone-numbers/{number_id}",
        headers=rival.headers,
    )
    assert other_tenant.status_code == 404  # NFR03-02: another business's profile
    removed = await client.delete(f"{operator.base}/{number_id}", headers=operator.headers)
    assert removed.status_code == 204
    assert (
        await db.scalar(select(PhoneNumber).where(PhoneNumber.id == uuid.UUID(number_id))) is None
    )
