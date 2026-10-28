"""SMS and email providers behind `Notifier` (FR-07, FR-06-04; ADR-0007). No real gateway
is called: Twilio is replaced by an httpx mock transport, SMTP by a fake class."""

import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx
import pytest

from app import cli
from app.core.config import Settings
from app.providers.notifications import (
    ChannelNotifier,
    LogNotifier,
    Notice,
    NotificationError,
    SmtpEmailSender,
    TwilioSmsSender,
    create_notifier,
    to_e164,
)

NOTICE = Notice(subject="Booking confirmed", body="See you Wednesday.", phone="0300-1234567")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+92 300 1234567", "+923001234567"),
        ("0300-1234567", "+923001234567"),
        ("0092 300 1234567", "+923001234567"),
        ("923001234567", "+923001234567"),
        ("3001234567", "+923001234567"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_numbers_become_international(raw: str, expected: str) -> None:
    assert to_e164(raw, "+92") == expected


def _twilio(handler: Any) -> TwilioSmsSender:
    return TwilioSmsSender(
        "AC123", "token-xyz", "+15005550006", "+92", transport=httpx.MockTransport(handler)
    )


async def test_twilio_posts_the_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"sid": "SM1"})

    sender = _twilio(handler)
    await sender.deliver("0300-1234567", "ignored subject", "Hello " * 400)
    await sender.aclose()

    [request] = seen
    assert request.url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert request.headers["authorization"].startswith("Basic ")
    form = parse_qs(request.content.decode())
    assert form["To"] == ["+923001234567"] and form["From"] == ["+15005550006"]
    assert len(form["Body"][0]) == 1600  # trimmed to Twilio's limit


async def test_twilio_errors_keep_the_code_but_not_the_body() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 21211, "message": "secret detail"})

    with pytest.raises(NotificationError) as caught:
        await _twilio(refused).deliver("0300", "s", "b")
    assert str(caught.value) == "SMS gateway refused it (HTTP 400, error 21211)"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(NotificationError, match="unreachable"):
        await _twilio(down).deliver("0300", "s", "b")


class FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []
    calls: ClassVar[list[str]] = []
    fail: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls.append(f"connect {host}:{port}")

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login {user}")

    def send_message(self, message: EmailMessage) -> None:
        if self.fail:
            raise smtplib.SMTPRecipientsRefused({})
        self.sent.append(message)


async def test_smtp_sends_an_email() -> None:
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail = [], [], False
    sender = SmtpEmailSender(
        "smtp.example.com",
        587,
        "AgentIQ <noreply@example.com>",
        username="bot",
        password="pw",
        smtp_class=FakeSMTP,
    )
    await sender.deliver("ali@example.com", "Booking confirmed", "See you.")
    assert FakeSMTP.calls == ["connect smtp.example.com:587", "starttls", "login bot"]
    [message] = FakeSMTP.sent
    assert message["To"] == "ali@example.com" and message["Subject"] == "Booking confirmed"

    FakeSMTP.fail = True
    with pytest.raises(NotificationError, match="SMTPRecipientsRefused"):
        await sender.deliver("ali@example.com", "s", "b")


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def deliver(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise NotificationError("down")
        self.sent.append(to)


async def test_channel_notifier_uses_each_contact_it_can_reach() -> None:
    sms, email = Recorder(), Recorder()
    both = Notice(subject="s", body="b", phone="+92 300 1", email="a@b.co")
    assert await ChannelNotifier(sms, email).send(both) == "sent"
    assert sms.sent == ["+92 300 1"] and email.sent == ["a@b.co"]

    # Email isn't configured: the phone still gets an SMS.
    assert await ChannelNotifier(sms, None).send(both) == "sent"
    # Only an email address, but only SMS is configured: logged, not sent.
    assert await ChannelNotifier(sms, None).send(Notice("s", "b", email="a@b.co")) == "logged"


async def test_channel_notifier_fails_only_when_every_channel_failed() -> None:
    both = Notice(subject="s", body="b", phone="+92 300 1", email="a@b.co")
    assert await ChannelNotifier(Recorder(fail=True), Recorder()).send(both) == "sent"
    with pytest.raises(NotificationError, match="down; down"):
        await ChannelNotifier(Recorder(fail=True), Recorder(fail=True)).send(both)


async def test_create_notifier_follows_the_settings() -> None:
    assert isinstance(create_notifier(Settings(_env_file=None)), LogNotifier)
    twilio = Settings(
        _env_file=None,
        twilio_account_sid="AC1",
        twilio_auth_token="t",
        twilio_from_number="+1500",
    )
    notifier = create_notifier(twilio)
    assert isinstance(notifier, ChannelNotifier)
    await notifier.aclose()
    # Half-configured Twilio (no sender number) is not used.
    half = Settings(_env_file=None, twilio_account_sid="AC1", twilio_auth_token="t")
    assert isinstance(create_notifier(half), LogNotifier)
    smtp = Settings(_env_file=None, smtp_host="smtp.example.com", smtp_from="x@example.com")
    assert isinstance(create_notifier(smtp), ChannelNotifier)
    blank = Settings(_env_file=None, smtp_host="  ", twilio_auth_token="")
    assert blank.smtp_host is None and blank.twilio_auth_token is None


async def test_check_notifications_command(capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(_env_file=None)
    assert await cli.check_notifications(settings, "+92 300 1234567", None, LogNotifier())
    assert "logged only" in capsys.readouterr().out
    failing = ChannelNotifier(Recorder(fail=True), None)
    assert not await cli.check_notifications(settings, "+92 300 1", None, failing)
    assert "failed: down" in capsys.readouterr().out
    assert await cli.check_notifications(
        settings, "+92 300 1", None, ChannelNotifier(Recorder(), None)
    )
    assert "sent." in capsys.readouterr().out


def test_follow_up_commands_parse() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["run-followups", "--once"]).once is True
    assert parser.parse_args(["run-followups"]).once is False
    args = parser.parse_args(["check-notifications", "--email", "a@b.co"])
    assert (args.phone, args.email) == (None, "a@b.co")
    assert cli.main(["check-notifications"]) == 1
