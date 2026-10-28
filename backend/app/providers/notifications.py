"""Messages to customers and businesses: follow-ups (FR-07) and escalation notices (FR-06-04).

`Notifier` is the interface the rest of the app uses. `create_notifier` builds one from the
settings (ADR-0007):

- SMS through Twilio's REST API when `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and
  `TWILIO_FROM_NUMBER` are set (analysis AS-9). Cost: Twilio's per-message price
  (roughly US$0.05-0.08 per SMS to Pakistan, 2026); a trial account only reaches
  verified numbers.
- Email over SMTP when `SMTP_HOST` is set (any provider: Gmail app password, Brevo,
  Mailgun...). Free tiers are enough for a prototype.
- Otherwise `LogNotifier`: the notice is written to the log with the contact masked and
  reported as "logged", so nobody mistakes it for a delivered message.

Twilio is called with plain httpx rather than its SDK: one POST doesn't justify a new
dependency (CLAUDE.md §6).
"""

import asyncio
import re
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Literal, Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

Delivery = Literal["logged", "sent"]

SEND_TIMEOUT_SECONDS = 10.0
SMS_MAX_CHARACTERS = 1600  # Twilio's limit for one message (it splits it into segments)


@dataclass(frozen=True, slots=True)
class Notice:
    subject: str
    body: str
    email: str | None = None
    phone: str | None = None


class NotificationError(Exception):
    """The notice couldn't be delivered. The message never includes a provider's response
    body or credentials, so it is safe to log and store."""


class Notifier(Protocol):
    async def send(self, notice: Notice) -> Delivery: ...


def mask_contact(value: str | None) -> str | None:
    """Enough to recognise a contact in the log without storing it (CLAUDE.md §4)."""
    if not value:
        return None
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:1]}***@{domain}"
    digits = [c for c in value if c.isdigit()]
    return f"***{''.join(digits[-3:])}"


def to_e164(phone: str, default_country_code: str) -> str:
    """ "+92 300 1234567", "0092 300..." and "0300-1234567" all become "+923001234567".

    Customers often give local numbers; SMS gateways need the international form."""
    digits = re.sub(r"\D", "", phone)
    country = re.sub(r"\D", "", default_country_code)
    if phone.strip().startswith("+"):
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith("0"):
        return f"+{country}{digits[1:]}"
    if country and digits.startswith(country) and len(digits) > 10:
        return f"+{digits}"
    return f"+{country}{digits}"


class LogNotifier:
    async def send(self, notice: Notice) -> Delivery:
        log.info(
            "notification_logged",
            subject=notice.subject,
            email=mask_contact(notice.email),
            phone=mask_contact(notice.phone),
        )
        return "logged"


class Sender(Protocol):
    """One delivery channel. Raises `NotificationError` when it fails."""

    async def deliver(self, to: str, subject: str, body: str) -> None: ...


class TwilioSmsSender:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        default_country_code: str,
        *,
        base_url: str = "https://api.twilio.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._path = f"/2010-04-01/Accounts/{account_sid}/Messages.json"
        self._from = from_number
        self._country = default_country_code
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=(account_sid, auth_token),
            timeout=SEND_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def deliver(self, to: str, subject: str, body: str) -> None:
        # SMS has no subject; the templates already name the business.
        form = {
            "To": to_e164(to, self._country),
            "From": self._from,
            "Body": body[:SMS_MAX_CHARACTERS],
        }
        try:
            response = await self._client.post(self._path, data=form)
        except httpx.HTTPError as exc:
            raise NotificationError(f"SMS gateway unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            # Twilio's error code (e.g. 21211 invalid number) is useful and safe to keep.
            code = _twilio_error_code(response)
            raise NotificationError(f"SMS gateway refused it (HTTP {response.status_code}{code})")

    async def aclose(self) -> None:
        await self._client.aclose()


def _twilio_error_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("code")
    except ValueError:
        return ""
    return f", error {int(code)}" if isinstance(code, int) else ""


class SmtpEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        *,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        smtp_class: Callable[..., Any] = smtplib.SMTP,  # replaceable in tests
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls
        self._smtp_class = smtp_class

    async def deliver(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await self.send_message(message)

    async def send_message(self, message: EmailMessage) -> None:
        """Sends a message as built, e.g. a reply with its own From and threading headers."""
        try:
            # smtplib blocks; keep it off the event loop (CLAUDE.md §4).
            await asyncio.to_thread(self._send_blocking, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotificationError(f"email server refused it ({type(exc).__name__})") from exc

    def _send_blocking(self, message: EmailMessage) -> None:
        with self._smtp_class(self._host, self._port, timeout=SEND_TIMEOUT_SECONDS) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password or "")
            smtp.send_message(message)


class ChannelNotifier:
    """Sends a notice to each contact it has (phone by SMS, address by email).

    "sent" when at least one got through; a contact whose channel isn't configured is only
    logged. Raises `NotificationError` when every configured channel failed."""

    def __init__(self, sms: Sender | None, email: Sender | None) -> None:
        self._channels: dict[str, Sender | None] = {"sms": sms, "email": email}
        self._log = LogNotifier()

    async def send(self, notice: Notice) -> Delivery:
        attempts = [
            (channel, contact, sender)
            for channel, contact in (("sms", notice.phone), ("email", notice.email))
            if contact and (sender := self._channels[channel]) is not None
        ]
        if not attempts:
            return await self._log.send(notice)
        errors: list[str] = []
        for channel, contact, sender in attempts:
            try:
                await sender.deliver(contact, notice.subject, notice.body)
            except NotificationError as exc:
                log.warning(
                    "notification_failed", channel=channel, to=mask_contact(contact), error=str(exc)
                )
                errors.append(str(exc))
                continue
            log.info("notification_sent", channel=channel, to=mask_contact(contact))
        if len(errors) == len(attempts):
            raise NotificationError("; ".join(errors))
        return "sent"

    async def aclose(self) -> None:
        for sender in self._channels.values():
            if isinstance(sender, TwilioSmsSender):
                await sender.aclose()


def create_notifier(settings: Settings) -> Notifier:
    sms: Sender | None = None
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        sms = TwilioSmsSender(
            settings.twilio_account_sid,
            settings.twilio_auth_token.get_secret_value(),
            settings.twilio_from_number,
            settings.sms_default_country_code,
        )
    email: Sender | None = None
    if settings.smtp_host and settings.smtp_from:
        email = SmtpEmailSender(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_from,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            starttls=settings.smtp_starttls,
        )
    if sms is None and email is None:
        return LogNotifier()
    return ChannelNotifier(sms, email)


async def close_notifier(notifier: Notifier) -> None:
    if isinstance(notifier, ChannelNotifier):
        await notifier.aclose()
