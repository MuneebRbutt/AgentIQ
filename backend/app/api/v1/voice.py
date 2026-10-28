"""Twilio's webhooks for phone calls (FR-02-01/04/05; ADR-0010). Not for the dashboard:
Twilio calls them, and each must carry a valid `X-Twilio-Signature`.

    POST /voice/incoming     a call starts (the number's "A call comes in" webhook)
    POST /voice/turn         the caller spoke
    POST /voice/silence      the caller said nothing
    POST /voice/status       the call's status changed (the number's status callback)
    GET  /voice/audio/{t}    a synthesised reply for <Play>

Every webhook answers TwiML with HTTP 200, even when something fails: Twilio would
otherwise play its own "application error" message to the caller (NFR04-01).
"""

from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.channels.twilio import twilio_params
from app.channels.voice.audio import AudioStore
from app.channels.voice.dependencies import get_audio_store, get_voice_service
from app.channels.voice.service import TROUBLE, VoiceService
from app.channels.voice.twiml import Hangup, Say, VoiceResponse
from app.core.errors import NotFoundError
from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"], include_in_schema=False)

Params = Annotated[dict[str, str], Depends(twilio_params)]
Service = Annotated[VoiceService, Depends(get_voice_service)]


async def _twiml(step: str, work: Awaitable[VoiceResponse]) -> Response:
    try:
        response = await work
    except Exception:
        log.exception("voice_webhook_failed", step=step)
        response = VoiceResponse().add(Say(TROUBLE), Hangup())
    return Response(content=response.xml(), media_type="application/xml")


@router.post("/incoming")
async def incoming(params: Params, service: Service) -> Response:
    return await _twiml("incoming", service.incoming(params))


@router.post("/turn")
async def turn(params: Params, service: Service) -> Response:
    return await _twiml("turn", service.turn(params))


@router.post("/silence")
async def silence(params: Params, service: Service) -> Response:
    return await _twiml("silence", service.silence(params))


@router.post("/status", status_code=status.HTTP_204_NO_CONTENT)
async def call_status(params: Params, service: Service) -> Response:
    await service.status(params)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audio/{token}")
async def audio(token: str, store: Annotated[AudioStore, Depends(get_audio_store)]) -> Response:
    clip = await store.get(token)
    if clip is None:
        raise NotFoundError("Audio not found or expired.")
    return Response(content=clip, media_type="audio/mpeg")
