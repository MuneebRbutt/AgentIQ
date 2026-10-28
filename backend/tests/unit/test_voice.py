"""The phone channel's pure parts: TwiML, webhook signatures, the speech adapters, number
normalisation and the latency table (FR-02, ADR-0010). Whole calls run in
tests/integration/test_voice.py."""

import json

import httpx
import pytest
from pydantic import ValidationError as SettingsError

from app.channels.twilio import is_valid_signature, twilio_signature
from app.channels.voice.commands import latency_table
from app.channels.voice.numbers import normalize_number
from app.channels.voice.service import RecordingError, TwilioRecordings
from app.channels.voice.twiml import (
    MAX_SPOKEN_CHARACTERS,
    Gather,
    Hangup,
    Play,
    Record,
    Redirect,
    Say,
    VoiceResponse,
    speakable,
)
from app.core.config import Environment, Settings
from app.core.errors import ValidationError
from app.providers.speech import (
    OpenAISpeech,
    SpeechError,
    WhisperTranscriber,
    create_speech_to_text,
    create_text_to_speech,
)

# Twilio's published example (twilio-python's request validator tests).
TWILIO_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
TWILIO_PARAMS = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+14158675309",
    "Digits": "1234",
    "From": "+14158675309",
    "To": "+18005551212",
}
RECORDING = "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE1"


# TwiML


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("**Booked!** Your reference is ABC123.", "Booked! Your reference is A B C 1 2 3."),
        ("- Monday: 12:00\n- Tuesday: 12:00", "Monday: 12:00 Tuesday: 12:00"),
        ("Call 2026 or PKR 12,400 at SPICE.", "Call 2026 or PKR 12,400 at SPICE."),
    ],
)
def test_replies_are_made_speakable(text: str, spoken: str) -> None:
    assert speakable(text) == spoken


def test_long_replies_are_cut_at_a_sentence() -> None:
    text = "This is a sentence. " * 200

    spoken = speakable(text)

    assert len(spoken) <= MAX_SPOKEN_CHARACTERS
    assert spoken.endswith(".")


def test_twiml_is_escaped_and_complete() -> None:
    xml = (
        VoiceResponse()
        .add(
            Gather(
                "https://voice.test/api/v1/voice/turn?a=1&b=2",
                Say("Fish & chips <today>", "Polly.Joanna", "en-US"),
                "en-US",
            ),
            Redirect("https://voice.test/api/v1/voice/silence"),
        )
        .xml()
    )

    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?><Response><Gather')
    assert 'action="https://voice.test/api/v1/voice/turn?a=1&amp;b=2"' in xml
    assert 'input="speech"' in xml and 'speechTimeout="auto"' in xml
    assert '<Say voice="Polly.Joanna" language="en-US">Fish &amp; chips &lt;today&gt;</Say>' in xml
    assert xml.endswith(
        '<Redirect method="POST">https://voice.test/api/v1/voice/silence</Redirect></Response>'
    )


def test_record_play_and_hangup() -> None:
    xml = VoiceResponse().add(Play("https://x/a.mp3"), Record("https://x/turn"), Hangup()).xml()

    assert "<Play>https://x/a.mp3</Play>" in xml
    assert 'maxLength="30"' in xml and 'playBeep="false"' in xml
    assert xml.endswith("<Hangup/></Response>")


# Signatures


def test_signature_matches_twilios_example() -> None:
    assert twilio_signature(TWILIO_URL, TWILIO_PARAMS, "12345") == "RSOYDt4T1cUTdK1PDd93/VVr8B8="


def test_signature_rejects_tampering() -> None:
    signature = twilio_signature(TWILIO_URL, TWILIO_PARAMS, "12345")

    assert is_valid_signature(TWILIO_URL, TWILIO_PARAMS, signature, "12345")
    assert not is_valid_signature(TWILIO_URL, {**TWILIO_PARAMS, "To": "+1999"}, signature, "12345")
    assert not is_valid_signature(TWILIO_URL + "&x=1", TWILIO_PARAMS, signature, "12345")
    assert not is_valid_signature(TWILIO_URL, TWILIO_PARAMS, signature, "other-token")
    assert not is_valid_signature(TWILIO_URL, TWILIO_PARAMS, None, "12345")


def test_production_refuses_unsigned_webhooks() -> None:
    with pytest.raises(SettingsError, match="TWILIO_VALIDATE_SIGNATURE"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            jwt_secret_key="x" * 40,
            twilio_validate_signature=False,
        )


# Speech adapters


async def test_whisper_posts_the_audio_and_returns_the_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " I'd like to book a table for two. "})

    stt = WhisperTranscriber(
        base_url="https://stt.test/v1/",
        api_key="key",
        model="whisper-1",
        language="en-IN",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    text = await stt.transcribe(b"RIFF....WAVE", "audio/wav")

    assert text == "I'd like to book a table for two."
    [request] = seen
    assert request.url == "https://stt.test/v1/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer key"
    body = request.content
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'name="language"\r\n\r\nen\r\n' in body  # ISO 639-1
    assert b'filename="call.wav"' in body and b"RIFF....WAVE" in body
    await stt.aclose()


@pytest.mark.parametrize(
    "response",
    [httpx.Response(500, text="secret transcript"), httpx.Response(200, json={"nope": 1})],
)
async def test_whisper_failures_raise_speech_error(response: httpx.Response) -> None:
    stt = WhisperTranscriber(
        base_url="https://stt.test/v1",
        api_key="key",
        model="whisper-1",
        language="en",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: response),
    )

    with pytest.raises(SpeechError) as caught:
        await stt.transcribe(b"x", "audio/wav")
    assert "secret transcript" not in str(caught.value)


async def test_tts_returns_mp3_audio() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=b"ID3audio")

    tts = OpenAISpeech(
        base_url="https://tts.test/v1",
        api_key="key",
        model="tts-1",
        voice="alloy",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    assert await tts.synthesize("Your table is confirmed for 7pm tomorrow.") == b"ID3audio"
    assert seen == [
        {
            "model": "tts-1",
            "voice": "alloy",
            "input": "Your table is confirmed for 7pm tomorrow.",
            "response_format": "mp3",
        }
    ]

    unreachable = OpenAISpeech(
        base_url="https://tts.test/v1",
        api_key="key",
        model="tts-1",
        voice="alloy",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(httpx.ConnectError("x"))),
    )
    with pytest.raises(SpeechError, match="unreachable"):
        await unreachable.synthesize("Hello")


def test_speech_providers_fall_back_to_twilio_without_a_key() -> None:
    assert create_speech_to_text(Settings(_env_file=None)) is None
    assert create_speech_to_text(Settings(_env_file=None, stt_provider="whisper")) is None
    assert create_text_to_speech(Settings(_env_file=None, tts_provider="openai")) is None
    configured = Settings(
        _env_file=None,
        stt_provider="whisper",
        stt_api_key="k",
        tts_provider="openai",
        tts_api_key="k",
    )
    assert isinstance(create_speech_to_text(configured), WhisperTranscriber)
    assert isinstance(create_text_to_speech(configured), OpenAISpeech)


# Recordings


async def test_recording_download_waits_for_twilio_then_succeeds() -> None:
    responses = iter([httpx.Response(404), httpx.Response(200, content=b"RIFFwav")])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(responses)

    recordings = TwilioRecordings("AC1", "token", transport=httpx.MockTransport(handler))

    audio, content_type = await recordings.fetch(RECORDING)

    assert (audio, content_type) == (b"RIFFwav", "audio/wav")
    assert [str(r.url) for r in seen] == [f"{RECORDING}.wav"] * 2
    assert seen[0].headers["Authorization"].startswith("Basic ")
    await recordings.aclose()


async def test_recording_from_another_host_is_never_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    recordings = TwilioRecordings("AC1", "token", transport=httpx.MockTransport(handler))

    with pytest.raises(RecordingError):
        await recordings.fetch("https://evil.example.com/recording")


# Numbers and latency


@pytest.mark.parametrize(
    ("raw", "number"),
    [
        ("+1 (415) 555-0123", "+14155550123"),
        ("042 3576 1234", "+924235761234"),
        ("0092 300 1234567", "+923001234567"),
    ],
)
def test_numbers_are_stored_in_e164(raw: str, number: str) -> None:
    assert normalize_number(raw, "+92") == number


def test_impossible_numbers_are_refused() -> None:
    with pytest.raises(ValidationError, match="full phone number"):
        normalize_number("12", "+92")


def test_latency_table_reports_each_stage() -> None:
    timings: list[dict[str, object]] = [
        {"stt_ms": None, "orchestrator_ms": 1000.0, "tts_ms": 200.0, "server_ms": 1300.0},
        {"stt_ms": None, "orchestrator_ms": 3000.0, "tts_ms": 400.0, "server_ms": 3500.0},
    ]

    lines = latency_table(timings)

    orchestrator = next(line for line in lines if line.startswith("Orchestrator"))
    assert orchestrator.split()[-4:] == ["2", "2000", "2000", "2900"]
    assert next(line for line in lines if line.startswith("Speech to text")).endswith("n/a")
