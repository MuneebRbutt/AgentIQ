"""A phone call, turn by turn (UC-02, FR-02-01/04/05/06; ADR-0010).

Each Twilio webhook becomes one step here and gets TwiML back:

    incoming  the call starts: route the dialled number to its profile, open a voice
              conversation, greet and listen
    turn      the caller spoke: get the words (Twilio's recogniser or Whisper), hand them to
              the orchestrator like any channel, speak the reply and listen again
    silence   the caller said nothing: ask again, then say goodbye
    status    the call ended: close the conversation (which lets follow-ups run)

Every failure still produces something to say (NFR04-01): a speech service that fails falls
back to Twilio's own recogniser or voice, and anything unexpected ends with an apology.
"""

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.voice.audio import AudioStore
from app.channels.voice.models import VoiceCall
from app.channels.voice.numbers import PhoneNumberService
from app.channels.voice.twiml import (
    Gather,
    Hangup,
    Play,
    Record,
    Redirect,
    Say,
    Speech,
    VoiceResponse,
    speakable,
)
from app.conversations.models import Interaction, Turn
from app.conversations.service import new_interaction
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.orchestrator.service import NormalizedRequest, Orchestrator
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig
from app.providers.notifications import mask_contact
from app.providers.speech import SpeechError, SpeechToText, TextToSpeech

log = get_logger(__name__)

NOT_CONNECTED = "Sorry, this number isn't connected to a business yet. Goodbye."
DIDNT_CATCH = "Sorry, I didn't catch that. Could you say it again?"
STILL_THERE = "Sorry, I didn't hear anything. How can I help you?"
GOODBYE = "Thank you for calling. Goodbye."
ENDED = "This call has been passed to our team, who will get back to you. Goodbye."
TROUBLE = (
    "Sorry, we're having trouble answering calls right now. Please try again in a few "
    "minutes. Goodbye."
)

ENDED_STATUSES = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})
_CALLER_ID = re.compile(r"^\+[1-9]\d{7,14}$")
_TWILIO_API = "https://api.twilio.com/"
RECORDING_ATTEMPTS = 3
RECORDING_RETRY_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class VoiceUrls:
    """Absolute URLs Twilio is sent to (relative ones would resolve against Twilio's own
    request URL, which is wrong behind a tunnel or proxy)."""

    base: str

    @property
    def turn(self) -> str:
        return f"{self.base}/api/v1/voice/turn"

    @property
    def silence(self) -> str:
        return f"{self.base}/api/v1/voice/silence"

    def audio(self, token: str) -> str:
        return f"{self.base}/api/v1/voice/audio/{token}"


class RecordingError(Exception):
    """The call recording couldn't be downloaded from Twilio."""


class TwilioRecordings:
    """Downloads a turn's recording for Whisper. Twilio may need a moment to finish
    writing it, so a 404 is retried briefly."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            auth=(account_sid, auth_token), timeout=timeout_seconds, transport=transport
        )

    async def fetch(self, recording_url: str) -> tuple[bytes, str]:
        if not recording_url.startswith(_TWILIO_API):
            raise RecordingError("not a Twilio recording URL")  # never fetch other hosts
        for attempt in range(RECORDING_ATTEMPTS):
            try:
                response = await self._http.get(f"{recording_url}.wav")
            except httpx.HTTPError as exc:
                raise RecordingError(f"Twilio unreachable: {type(exc).__name__}") from exc
            if response.status_code == httpx.codes.NOT_FOUND and attempt < RECORDING_ATTEMPTS - 1:
                await asyncio.sleep(RECORDING_RETRY_SECONDS)
                continue
            if response.status_code >= httpx.codes.BAD_REQUEST:
                raise RecordingError(f"Twilio returned HTTP {response.status_code}")
            return response.content, "audio/wav"
        raise RecordingError("recording not ready")  # pragma: no cover  # loop always returns

    async def aclose(self) -> None:
        await self._http.aclose()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


class VoiceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        orchestrator: Orchestrator,
        audio: AudioStore,
        urls: VoiceUrls,
        *,
        speech_to_text: SpeechToText | None = None,
        text_to_speech: TextToSpeech | None = None,
        recordings: TwilioRecordings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._orchestrator = orchestrator
        self._audio = audio
        self._urls = urls
        # Whisper needs the recording, so it is used only when it can be downloaded.
        self._stt = speech_to_text if recordings is not None else None
        self._tts = text_to_speech
        self._recordings = recordings

    # --- webhooks ---------------------------------------------------------------------

    async def incoming(self, params: dict[str, str]) -> VoiceResponse:
        call_sid = params.get("CallSid", "")
        async with self._session_factory() as db:
            existing = await self._call(db, call_sid)
            if existing is not None:  # Twilio retried the webhook
                config = await self._config(db, existing.profile_id)
                return await self._listen(config.assistant.greeting)
            number = await PhoneNumberService(db, self._settings.sms_default_country_code).route(
                params.get("To", "")
            )
            if number is None or not call_sid:
                log.info("voice_number_not_connected", to=mask_contact(params.get("To")))
                return VoiceResponse().add(self._say(NOT_CONNECTED), Hangup())
            config = await self._config(db, number.profile_id)
            caller = params.get("From", "")
            now = self._clock.now()
            interaction = new_interaction(
                number.tenant_id,
                number.profile_id,
                "voice",
                caller if _CALLER_ID.match(caller) else None,
                now,
            )
            db.add(interaction)
            await db.flush()  # no ORM relationship orders the inserts, so the row goes first
            db.add(
                VoiceCall(
                    tenant_id=number.tenant_id,
                    profile_id=number.profile_id,
                    interaction_id=interaction.id,
                    call_sid=call_sid,
                    status="in_progress",
                    silences=0,
                    timings=[],
                    started_at=now,
                )
            )
            await db.commit()
            log.info(
                "voice_call_started",
                profile_id=str(number.profile_id),
                session_id=str(interaction.id),
                caller=mask_contact(caller),
            )
        return await self._listen(config.assistant.greeting)

    async def turn(self, params: dict[str, str]) -> VoiceResponse:
        started = time.perf_counter()
        async with self._session_factory() as db:
            call = await self._call(db, params.get("CallSid", ""))
            if call is None:
                return VoiceResponse().add(self._say(TROUBLE), Hangup())
            heard = await self._heard(params)
            if heard is None:
                return await self._listen(DIDNT_CATCH)
            text, stt_ms = heard
            if not text:
                return await self._silence(db, call)
            call.silences = 0
            await db.commit()
            tenant_id, profile_id, session_id = call.tenant_id, call.profile_id, call.interaction_id

        orchestrator_started = time.perf_counter()
        try:
            outcome = await self._orchestrator.handle(
                NormalizedRequest(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    session_id=session_id,
                    channel="voice",
                    text=text[:2000],
                )
            )
        except ConflictError:  # handed over or closed while the call was still open
            return VoiceResponse().add(self._say(ENDED), Hangup())
        orchestrator_ms = _ms(orchestrator_started)

        tts_started = time.perf_counter()
        speech = await self._speech(outcome.reply)
        timings: dict[str, Any] = {
            "stt": self._stt.name if self._stt else "twilio",
            "tts": self._tts.name if self._tts else "twilio",
            "stt_ms": stt_ms,
            "orchestrator_ms": orchestrator_ms,
            "tts_ms": _ms(tts_started) if self._tts else None,
            "server_ms": _ms(started),
        }
        await self._record_timings(session_id, timings)
        if outcome.handed_off:
            return VoiceResponse().add(speech, Hangup())
        return self._listen_after(speech)

    async def silence(self, params: dict[str, str]) -> VoiceResponse:
        async with self._session_factory() as db:
            call = await self._call(db, params.get("CallSid", ""))
            if call is None:
                return VoiceResponse().add(self._say(GOODBYE), Hangup())
            return await self._silence(db, call)

    async def status(self, params: dict[str, str]) -> None:
        call_status = params.get("CallStatus", "")
        if call_status not in ENDED_STATUSES:
            return
        async with self._session_factory() as db:
            call = await self._call(db, params.get("CallSid", ""))
            if call is None or call.status == "ended":
                return
            now = self._clock.now()
            call.status = "ended"
            call.end_reason = call_status
            call.ended_at = now
            interaction = await db.get(Interaction, call.interaction_id)
            if interaction is not None and interaction.status == "open":
                # An ended call is an ended conversation: feedback can follow (FR-07-04).
                interaction.status = "closed"
                interaction.updated_at = now
            await db.commit()
            log.info("voice_call_ended", session_id=str(call.interaction_id), reason=call_status)

    # --- steps ------------------------------------------------------------------------

    async def _heard(self, params: dict[str, str]) -> tuple[str, float | None] | None:
        """What the caller said and how long recognising it took here; "" for nothing,
        None when it couldn't be recognised."""
        if "SpeechResult" in params:  # Twilio's recogniser; its time is spent on Twilio's side
            return params["SpeechResult"].strip(), None
        recording = params.get("RecordingUrl")
        if not recording or params.get("RecordingDuration") == "0":
            return "", None
        if self._stt is None or self._recordings is None:
            return None
        started = time.perf_counter()
        try:
            audio, content_type = await self._recordings.fetch(recording)
            text = await self._stt.transcribe(audio, content_type)
        except (RecordingError, SpeechError) as exc:
            log.warning("voice_stt_failed", error=str(exc))
            return None
        return text, _ms(started)

    async def _silence(self, db: AsyncSession, call: VoiceCall) -> VoiceResponse:
        call.silences += 1
        await db.commit()
        if call.silences > self._settings.voice_max_silences:
            return VoiceResponse().add(self._say(GOODBYE), Hangup())
        return await self._listen(STILL_THERE)

    async def _listen(self, text: str) -> VoiceResponse:
        return self._listen_after(await self._speech(text))

    def _listen_after(self, speech: Speech) -> VoiceResponse:
        if self._stt is None:
            # The caller can talk over the prompt (barge-in).
            gather = Gather(self._urls.turn, speech, self._settings.voice_language)
            return VoiceResponse().add(gather, Redirect(self._urls.silence))
        return VoiceResponse().add(speech, Record(self._urls.turn), Redirect(self._urls.silence))

    async def _speech(self, text: str) -> Speech:
        spoken = speakable(text)
        if self._tts is not None:
            try:
                token = await self._audio.put(await self._tts.synthesize(spoken))
                return Play(self._urls.audio(token))
            except SpeechError as exc:  # UC-02 exception: fall back to Twilio's voice
                log.warning("voice_tts_failed", error=str(exc))
        return self._say(spoken)

    def _say(self, text: str) -> Say:
        return Say(text, self._settings.voice_say_voice, self._settings.voice_language)

    # --- storage ----------------------------------------------------------------------

    async def _call(self, db: AsyncSession, call_sid: str) -> VoiceCall | None:
        if not call_sid:
            return None
        return await db.scalar(select(VoiceCall).where(VoiceCall.call_sid == call_sid))

    async def _config(self, db: AsyncSession, profile_id: uuid.UUID) -> BusinessProfileConfig:
        profile = await db.get(BusinessProfile, profile_id)
        if profile is None:  # pragma: no cover  # cascades remove numbers with the profile
            raise LookupError("profile of a connected number is gone")
        return BusinessProfileConfig.model_validate(profile.config)

    async def _record_timings(self, session_id: uuid.UUID, timings: dict[str, Any]) -> None:
        """Per-stage timings on the call (latency report) and on the assistant's turn, so
        the conversation log shows them next to the orchestrator's (FR-02-06)."""
        async with self._session_factory() as db:
            call = await db.scalar(select(VoiceCall).where(VoiceCall.interaction_id == session_id))
            if call is not None:
                call.timings = [*call.timings, timings]
            turn = await db.scalar(
                select(Turn)
                .where(Turn.interaction_id == session_id, Turn.role == "assistant")
                .order_by(Turn.seq.desc())
                .limit(1)
            )
            if turn is not None:
                speech = {"stt": timings["stt_ms"], "tts": timings["tts_ms"]}
                stages = {name: ms for name, ms in speech.items() if ms is not None}
                turn.data = {**turn.data, "timings": {**turn.data.get("timings", {}), **stages}}
            await db.commit()
