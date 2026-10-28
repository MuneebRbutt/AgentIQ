"""A text message from a customer, answered in a text message (FR-02-02; ADR-0011).

Twilio posts each message to the number's "A message comes in" webhook; the reply goes back
in the response as TwiML `<Message>`, so it costs one outgoing SMS and needs no API call.
The number routes the message to its business exactly as it routes calls (ADR-0010).
"""

import re
from xml.sax.saxutils import escape

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.inbound import Inbound, InboundHandler
from app.channels.voice.numbers import PhoneNumberService
from app.channels.voice.twiml import plain_text
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.notifications import SMS_MAX_CHARACTERS, mask_contact

log = get_logger(__name__)

TEXT_ONLY = "Sorry, I can only read text. Please type your message."
# Twilio handles these itself (opt-out and help keywords) and blocks any reply to them.
# https://help.twilio.com/articles/223134027
CARRIER_KEYWORDS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "start", "yes", "unstop"}
    | {"help", "info"}
)
_SENDER = re.compile(r"^\+[1-9]\d{7,14}$")


def message_twiml(text: str | None) -> str:
    """A reply, or an empty response (Twilio then sends nothing)."""
    body = f"<Message>{escape(text)}</Message>" if text else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def sms_text(reply: str) -> str:
    text = plain_text(reply)
    if len(text) <= SMS_MAX_CHARACTERS:
        return text
    cut = text[: SMS_MAX_CHARACTERS - 1]
    return cut[: cut.rfind(" ")] + "…"


class SmsService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        handler: InboundHandler,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._handler = handler

    async def incoming(self, params: dict[str, str]) -> str | None:
        """The reply to send, or None for none."""
        sender = params.get("From", "")
        message_sid = params.get("MessageSid") or params.get("SmsSid") or ""
        async with self._session_factory() as db:
            number = await PhoneNumberService(db, self._settings.sms_default_country_code).route(
                params.get("To", "")
            )
        if number is None or not message_sid or not _SENDER.match(sender):
            log.info(
                "sms_not_routed", to=mask_contact(params.get("To")), sender=mask_contact(sender)
            )
            return None
        body = params.get("Body", "").strip()
        if body.lower().strip(" .!") in CARRIER_KEYWORDS:
            return None
        if not body:  # a picture or other media only
            return TEXT_ONLY
        result = await self._handler.handle(
            Inbound(
                tenant_id=number.tenant_id,
                profile_id=number.profile_id,
                channel="sms",
                external_id=message_sid,
                sender=sender,
                text=body,
            )
        )
        if result.reply is None:
            return None
        log.info("sms_answered", session_id=str(result.session_id), sender=mask_contact(sender))
        return sms_text(result.reply)
