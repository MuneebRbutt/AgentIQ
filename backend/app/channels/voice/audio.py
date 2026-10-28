"""Synthesised replies waiting for Twilio to fetch them (FR-02-05; ADR-0010).

`<Play>` needs a URL, so each reply's audio is kept in Redis under a random token for a
few minutes and served by `GET /api/v1/voice/audio/{token}`. Redis rather than memory, so
any API process can serve it; it is a cache, and a lost clip only costs one fallback to
`<Say>` on Twilio's side.
"""

import base64
import re
import secrets

from redis.asyncio import Redis

AUDIO_TTL_SECONDS = 15 * 60
_PREFIX = "voice:audio:"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


class AudioStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def put(self, audio: bytes) -> str:
        token = secrets.token_urlsafe(32)
        # The client decodes responses as text, so the bytes are stored base64-encoded.
        await self._redis.set(
            _PREFIX + token, base64.b64encode(audio).decode(), ex=AUDIO_TTL_SECONDS
        )
        return token

    async def get(self, token: str) -> bytes | None:
        if not _TOKEN.match(token):
            return None
        stored = await self._redis.get(_PREFIX + token)
        return base64.b64decode(stored) if stored else None
