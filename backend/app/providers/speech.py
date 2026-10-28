"""Speech adapters for phone calls: speech to text (FR-02-04) and text to speech (FR-02-05).

Both are optional (ADR-0010). Without them the voice channel uses Twilio's own speech
recognition (`<Gather input="speech">`) and voice (`<Say>`), which need no extra key. With
them:

- `WhisperTranscriber` posts the recorded call audio to any OpenAI-compatible
  `/audio/transcriptions` endpoint (OpenAI `whisper-1`, Groq `whisper-large-v3`). Cost
  note: OpenAI charges about US$0.006 a minute of audio (2026); Groq is cheaper.
- `OpenAISpeech` asks an OpenAI-compatible `/audio/speech` endpoint for an MP3 (`tts-1`,
  the low-latency model, analysis Q-3). Cost note: about US$15 per million characters, so
  a 200-character reply costs a fraction of a cent.

Tests use fakes; they never call a paid API. Errors never carry the provider's response
body, which may echo what the caller said.
"""

from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


class SpeechError(Exception):
    """The speech service couldn't be reached or returned something unusable."""


class SpeechToText(Protocol):
    name: str

    async def transcribe(self, audio: bytes, content_type: str) -> str: ...

    async def aclose(self) -> None: ...


class TextToSpeech(Protocol):
    name: str
    content_type: str

    async def synthesize(self, text: str) -> bytes: ...

    async def aclose(self) -> None: ...


def _client(
    base_url: str, api_key: str, timeout: float, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
        transport=transport,
    )


async def _send(client: httpx.AsyncClient, service: str, request: httpx.Request) -> httpx.Response:
    try:
        response = await client.send(request)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.warning(f"{service}_http_error", status=exc.response.status_code)
        raise SpeechError(f"{service} returned HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        log.warning(f"{service}_unreachable", error=type(exc).__name__)
        raise SpeechError(f"{service} unreachable: {type(exc).__name__}") from exc
    return response


class WhisperTranscriber:
    name = "whisper"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        language: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._language = language.split("-")[0]  # Whisper takes ISO 639-1: "en"
        self._http = _client(base_url, api_key, timeout_seconds, transport)

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        extension = "mp3" if "mpeg" in content_type else "wav"
        request = self._http.build_request(
            "POST",
            "/audio/transcriptions",
            files={"file": (f"call.{extension}", audio, content_type)},
            data={"model": self._model, "language": self._language, "response_format": "json"},
        )
        response = await _send(self._http, "stt", request)
        try:
            text = response.json()["text"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SpeechError("stt returned an unexpected response") from exc
        if not isinstance(text, str):
            raise SpeechError("stt returned an unexpected response")
        return text.strip()

    async def aclose(self) -> None:
        await self._http.aclose()


class OpenAISpeech:
    name = "openai"
    content_type = "audio/mpeg"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._voice = voice
        self._http = _client(base_url, api_key, timeout_seconds, transport)

    async def synthesize(self, text: str) -> bytes:
        request = self._http.build_request(
            "POST",
            "/audio/speech",
            json={
                "model": self._model,
                "voice": self._voice,
                "input": text,
                "response_format": "mp3",
            },
        )
        response = await _send(self._http, "tts", request)
        if not response.content:
            raise SpeechError("tts returned no audio")
        return response.content

    async def aclose(self) -> None:
        await self._http.aclose()


def create_speech_to_text(settings: Settings) -> SpeechToText | None:
    """Whisper when chosen and a key is set; None = Twilio's recogniser."""
    if settings.stt_provider != "whisper":
        return None
    if settings.stt_api_key is None:
        log.warning("stt_key_missing", fallback="twilio")
        return None
    return WhisperTranscriber(
        base_url=settings.stt_base_url,
        api_key=settings.stt_api_key.get_secret_value(),
        model=settings.stt_model,
        language=settings.voice_language,
        timeout_seconds=settings.speech_timeout_seconds,
    )


def create_text_to_speech(settings: Settings) -> TextToSpeech | None:
    """An OpenAI-compatible voice when chosen and a key is set; None = Twilio's <Say>."""
    if settings.tts_provider != "openai":
        return None
    if settings.tts_api_key is None:
        log.warning("tts_key_missing", fallback="twilio")
        return None
    return OpenAISpeech(
        base_url=settings.tts_base_url,
        api_key=settings.tts_api_key.get_secret_value(),
        model=settings.tts_model,
        voice=settings.tts_voice,
        timeout_seconds=settings.speech_timeout_seconds,
    )
