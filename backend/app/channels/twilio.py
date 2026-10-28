"""Twilio webhooks, shared by phone calls and text messages: checking that a request really
comes from Twilio (NFR03; analysis M-15).

Twilio signs each request: HMAC-SHA1, keyed with the account's auth token, over the full
URL it called followed by every POST parameter (name then value, sorted by name), base64
encoded into the `X-Twilio-Signature` header.
https://www.twilio.com/docs/usage/webhooks/webhooks-security
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping
from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import PermissionDeniedError
from app.core.logging import get_logger

log = get_logger(__name__)


def twilio_signature(url: str, params: Mapping[str, str], auth_token: str) -> str:
    payload = url + "".join(f"{name}{params[name]}" for name in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid_signature(
    url: str, params: Mapping[str, str], signature: str | None, auth_token: str
) -> bool:
    if not signature:
        return False
    expected = twilio_signature(url, params, auth_token)
    return hmac.compare_digest(expected, signature)


def public_base_url(request: Request, settings: Settings) -> str:
    return settings.public_base_url or str(request.base_url).rstrip("/")


async def twilio_params(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, str]:
    """The webhook's form fields, once its Twilio signature checks out (NFR03)."""
    form = await request.form()
    params = {key: value for key, value in form.items() if isinstance(value, str)}
    if not settings.twilio_validate_signature:
        return params
    token = settings.twilio_auth_token
    url = public_base_url(request, settings) + request.url.path
    if request.url.query:
        url += f"?{request.url.query}"
    signature = request.headers.get("X-Twilio-Signature")
    if token is None or not is_valid_signature(url, params, signature, token.get_secret_value()):
        log.warning(
            "twilio_signature_rejected", path=request.url.path, configured=token is not None
        )
        raise PermissionDeniedError("Invalid Twilio signature.")
    return params
