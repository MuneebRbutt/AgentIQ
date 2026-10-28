"""FastAPI dependencies for the phone channel."""

from typing import Annotated

from fastapi import Depends, Request

from app.channels.twilio import public_base_url
from app.channels.voice.audio import AudioStore
from app.channels.voice.numbers import PhoneNumberService
from app.channels.voice.service import TwilioRecordings, VoiceService, VoiceUrls
from app.conversations.dependencies import get_orchestrator
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.db.session import DbSession
from app.orchestrator.service import Orchestrator
from app.providers.speech import SpeechToText, TextToSpeech


def get_audio_store(request: Request) -> AudioStore:
    return AudioStore(request.app.state.redis)


def get_voice_service(
    request: Request,
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
    audio: Annotated[AudioStore, Depends(get_audio_store)],
) -> VoiceService:
    state = request.app.state
    speech_to_text: SpeechToText | None = state.speech_to_text
    text_to_speech: TextToSpeech | None = state.text_to_speech
    recordings: TwilioRecordings | None = state.twilio_recordings
    return VoiceService(
        state.session_factory,
        clock,
        settings,
        orchestrator,
        audio,
        VoiceUrls(public_base_url(request, settings)),
        speech_to_text=speech_to_text,
        text_to_speech=text_to_speech,
        recordings=recordings,
    )


def get_phone_number_service(
    db: DbSession, settings: Annotated[Settings, Depends(get_settings)]
) -> PhoneNumberService:
    return PhoneNumberService(db, settings.sms_default_country_code)
