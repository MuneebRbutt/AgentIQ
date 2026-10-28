"""`python -m app.cli connect-number`, `voice-latency` and `check-voice` (FR-02; ADR-0010)."""

import asyncio
import statistics
import time
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.channels.voice.models import VoiceCall
from app.channels.voice.numbers import PhoneNumberService
from app.core.clock import SystemClock
from app.core.config import PROJECT_ROOT, Settings
from app.core.errors import NotFoundError
from app.db.session import create_engine, create_session_factory
from app.evaluation.metrics import percentile
from app.profiles.models import BusinessProfile
from app.providers.speech import SpeechError, create_speech_to_text, create_text_to_speech

# TC-02 step 3's reply.
SAMPLE_REPLY = "Your table is confirmed for 7pm tomorrow."
CHECK_AUDIO = PROJECT_ROOT / "backend" / "data" / "voice-check.mp3"
STAGES = {
    "stt_ms": "Speech to text (Whisper; Twilio's recogniser isn't timed here)",
    "orchestrator_ms": "Orchestrator (understand, agents, reply)",
    "tts_ms": "Text to speech",
    "server_ms": "Whole webhook (what the caller waits for after Twilio hears them)",
}


async def connect_number(settings: Settings, profile_id: uuid.UUID, number: str) -> str:
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            profile = await db.get(BusinessProfile, profile_id)
            if profile is None:
                raise NotFoundError("Business profile not found.")
            row = await PhoneNumberService(db, settings.sms_default_country_code).add(
                profile, number
            )
            return row.number
    finally:
        await engine.dispose()


def latency_table(timings: list[dict[str, object]]) -> list[str]:
    """Mean, p50 and p95 per stage over every answered turn (FR-02-06, NFR01-01)."""
    lines = [f"{'Stage':<70} {'turns':>5} {'mean':>7} {'p50':>7} {'p95':>7}"]
    for key, label in STAGES.items():
        values = [float(v) for t in timings if isinstance(v := t.get(key), int | float)]
        if not values:
            lines.append(f"{label:<70} {0:>5} {'n/a':>7} {'n/a':>7} {'n/a':>7}")
            continue
        p50, p95 = percentile(values, 0.5), percentile(values, 0.95)
        lines.append(
            f"{label:<70} {len(values):>5} {statistics.fmean(values):>7.0f} "
            f"{p50 or 0:>7.0f} {p95 or 0:>7.0f}"
        )
    return lines


async def voice_latency(settings: Settings, profile_id: uuid.UUID | None, days: int) -> None:
    since = SystemClock().now() - timedelta(days=days)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as db:
            statement = select(VoiceCall).where(VoiceCall.started_at >= since)
            if profile_id is not None:
                statement = statement.where(VoiceCall.profile_id == profile_id)
            calls = list(await db.scalars(statement))
    finally:
        await engine.dispose()
    timings = [t for call in calls for t in call.timings]
    print(f"{len(calls)} calls, {len(timings)} answered turns in the last {days} days (ms):")
    for line in latency_table(timings):
        print(line)


def _save(path: Path, audio: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)


async def check_voice(settings: Settings, text: str, output: Path = CHECK_AUDIO) -> bool:
    """Speaks `text` with the configured voice and, if Whisper is set up, transcribes the
    result back (TC-02 steps 2-3 without a phone)."""
    tts = create_text_to_speech(settings)
    stt = create_speech_to_text(settings)
    if tts is None:
        print(
            f"Text to speech: Twilio <Say> ({settings.voice_say_voice}); it runs on Twilio's "
            "side, so place a test call to hear it. Set TTS_PROVIDER=openai and "
            "TTS_API_KEY to check an OpenAI-compatible voice here."
        )
        if stt is None:
            print("Speech to text: Twilio's recogniser (runs on Twilio's side).")
        return True
    ok = True
    try:
        started = time.perf_counter()
        audio = await tts.synthesize(text)
        tts_ms = (time.perf_counter() - started) * 1000
        await asyncio.to_thread(_save, output, audio)
        print(f"Text to speech ({tts.name}): {len(audio):,} bytes in {tts_ms:.0f} ms -> {output}")
        if stt is None:
            print("Speech to text: Twilio's recogniser (set STT_PROVIDER=whisper to check one).")
        else:
            started = time.perf_counter()
            heard = await stt.transcribe(audio, tts.content_type)
            stt_ms = (time.perf_counter() - started) * 1000
            print(f"Speech to text ({stt.name}) in {stt_ms:.0f} ms: {heard!r}")
    except SpeechError as exc:
        print(f"Failed: {exc}. Check the TTS_* / STT_* settings in .env.")
        ok = False
    finally:
        await tts.aclose()
        if stt is not None:
            await stt.aclose()
    return ok
