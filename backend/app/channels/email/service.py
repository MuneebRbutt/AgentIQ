"""An email from a customer, answered by email (FR-02-03; ADR-0011).

`EmailPoller` reads the shared mailbox every `EMAIL_POLL_SECONDS`. `EmailChannel` routes each
message to the business whose address it was sent to, has the orchestrator answer it like any
channel, and replies from that address in the same thread. The reply is marked
`Auto-Submitted: auto-replied`, so other robots don't answer it (RFC 3834).
"""

import asyncio
import contextlib
from collections import Counter
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.email.addresses import EmailAddressService
from app.channels.email.mailbox import Mailbox
from app.channels.email.parsing import ParsedEmail, parse_email
from app.channels.inbound import Inbound, InboundHandler
from app.channels.voice.twiml import plain_text
from app.core.config import Settings
from app.core.logging import get_logger
from app.profiles.models import BusinessProfile
from app.providers.notifications import NotificationError, mask_contact

log = get_logger(__name__)


class ReplySender(Protocol):
    async def send_message(self, message: EmailMessage) -> None: ...


def reply_message(original: ParsedEmail, business: str, address: str, reply: str) -> EmailMessage:
    subject = original.subject or "your message"
    message = EmailMessage()
    message["From"] = formataddr((business, address))
    message["To"] = original.sender
    message["Subject"] = subject if original.is_reply else f"Re: {subject}"
    message["Message-ID"] = make_msgid(domain=address.partition("@")[2])
    message["In-Reply-To"] = original.message_id
    earlier = original.references or original.in_reply_to or ""
    message["References"] = f"{earlier} {original.message_id}".strip()
    message["Auto-Submitted"] = "auto-replied"
    message.set_content(plain_text(reply))
    return message


class EmailChannel:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        handler: InboundHandler,
        replies: ReplySender,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._handler = handler
        self._replies = replies

    async def process(self, raw: bytes) -> str:
        """Handles one message; returns what happened, for the log."""
        email = parse_email(raw)
        if email is None:
            return "unreadable"
        async with self._session_factory() as db:
            addresses = EmailAddressService(db)
            own = await addresses.all_addresses()
            if self._settings.imap_username:
                own.add(self._settings.imap_username.lower())
            if email.sender in own:
                return "own"  # never answer ourselves
            if email.automated:
                return "automated"
            route = await addresses.route(email.recipients)
            if route is None:
                log.info("email_not_routed", sender=mask_contact(email.sender))
                return "not_routed"
            profile = await db.get(BusinessProfile, route.profile_id)
            business = profile.name if profile else ""
            tenant_id, profile_id, address = route.tenant_id, route.profile_id, route.address
        text = email.text
        if not text:
            return "empty"
        result = await self._handler.handle(
            Inbound(
                tenant_id=tenant_id,
                profile_id=profile_id,
                channel="email",
                external_id=email.message_id,
                sender=email.sender,
                text=text,
            )
        )
        if result.reply is None:
            return result.kind
        try:
            await self._replies.send_message(reply_message(email, business, address, result.reply))
        except NotificationError as exc:
            log.warning("email_reply_failed", session_id=str(result.session_id), error=str(exc))
            return "reply_failed"
        log.info(
            "email_answered", session_id=str(result.session_id), sender=mask_contact(email.sender)
        )
        return "answered"


class EmailPoller:
    def __init__(self, mailbox: Mailbox, channel: EmailChannel, poll_seconds: float) -> None:
        self._mailbox = mailbox
        self._channel = channel
        self._poll_seconds = poll_seconds

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("email_poller_started", poll_seconds=self._poll_seconds)
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # never let one bad pass stop the poller (NFR04-01)
                log.exception("email_pass_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._poll_seconds)
        log.info("email_poller_stopped")

    async def run_once(self) -> Counter[str]:
        report: Counter[str] = Counter()
        try:
            messages = await asyncio.to_thread(self._mailbox.fetch_unseen)
        except Exception as exc:  # a mailbox that is down is retried on the next pass
            log.warning("email_mailbox_unavailable", error=type(exc).__name__)
            return report
        for _, raw in messages:
            try:
                report[await self._channel.process(raw)] += 1
            except Exception:
                # Marked read anyway: a message that always fails must not block the rest.
                log.exception("email_processing_failed")
                report["failed"] += 1
        if messages:
            await asyncio.to_thread(self._mailbox.mark_seen, [uid for uid, _ in messages])
            log.info("email_pass", **report)
        return report
