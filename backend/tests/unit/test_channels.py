"""The text-message and email channels' pure parts: reading emails, the IMAP mailbox, SMS
replies, and serving the dashboard (FR-02-02/03, FR-10-08; ADR-0011, ADR-0012). Whole
conversations run in tests/integration/test_channels.py."""

from email.message import EmailMessage
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from fastapi import FastAPI

from app.channels.email.mailbox import ImapMailbox, MailboxError
from app.channels.email.parsing import html_to_text, parse_email, strip_quoted
from app.channels.email.service import reply_message
from app.channels.sms.service import message_twiml, sms_text
from app.core.config import Settings
from app.core.spa import mount_dashboard
from app.providers.notifications import SMS_MAX_CHARACTERS


def _raw(body: str = "Is your food halal?", **headers: str) -> bytes:
    message = EmailMessage()
    values = {
        "From": "Ayesha Khan <Ayesha@Example.org>",
        "To": "Spice <agentiq+spice@example.com>",
        "Subject": "Halal food?",
        "Message-ID": "<m1@example.org>",
    }
    values.update({name.replace("_", "-"): value for name, value in headers.items()})
    for name, value in values.items():
        if value:
            message[name] = value
    message.set_content(body)
    return message.as_bytes()


# Reading emails


def test_an_email_is_read_into_sender_recipients_and_text() -> None:
    email = parse_email(_raw(Cc="other@example.com", Delivered_To="agentiq+spice@example.com"))

    assert email is not None
    assert email.sender == "ayesha@example.org"
    assert email.recipients == ["agentiq+spice@example.com", "other@example.com"]
    assert (email.message_id, email.automated, email.is_reply) == ("<m1@example.org>", False, False)
    assert email.text == "Halal food?\n\nIs your food halal?"


@pytest.mark.parametrize(
    ("subject", "body", "text"),
    [
        ("Re: Halal food?", "And parking?", "And parking?"),
        ("Booking", "Booking for two at 7pm", "Booking for two at 7pm"),  # already said
        ("", "Table for two?", "Table for two?"),
        ("Table for two tonight?", "", "Table for two tonight?"),
    ],
)
def test_the_subject_is_added_only_when_it_says_something(
    subject: str, body: str, text: str
) -> None:
    email = parse_email(_raw(body, Subject=subject))

    assert email is not None and email.text == text


@pytest.mark.parametrize(
    "reply",
    [
        "And parking?\n\nOn Tue, 21 Oct 2026 at 09:00, Spice Garden <a@b.c> wrote:\n> Yes.",
        "And parking?\n\nOn Tue, 21 Oct 2026 at 09:00, Spice Garden <\na@b.c> wrote:\n> Yes.",
        "And parking?\n\n-----Original Message-----\nFrom: Spice\nYes.",
        "And parking?\n\nFrom: Spice Garden\nSent: Tuesday\nTo: me\n\nYes.",
        "And parking?\n> quoted line\n\n-- \nAyesha\n0300 1234567",
    ],
)
def test_quoted_history_and_signatures_are_cut(reply: str) -> None:
    assert strip_quoted(reply) == "And parking?"


def test_html_only_email_is_reduced_to_its_text() -> None:
    html = (
        "<html><head><style>p{color:red}</style></head><body><p>Table for two?</p>"
        "<div>Thanks<br>Ayesha</div><blockquote>Earlier message</blockquote></body></html>"
    )
    message = EmailMessage()
    message["From"] = "ayesha@example.org"
    message["To"] = "agentiq+spice@example.com"
    message.set_content(html, subtype="html")

    email = parse_email(message.as_bytes())

    assert html_to_text("<p>a</p><script>x()</script>b") == "\na\nb"
    assert email is not None and email.body == "Table for two?\n\nThanks\nAyesha"


@pytest.mark.parametrize(
    "headers",
    [
        {"Auto_Submitted": "auto-replied"},
        {"Precedence": "bulk"},
        {"List_Unsubscribe": "<mailto:u@x>"},
        {"From": "MAILER-DAEMON@example.com"},
        {"From": "no-reply@shop.example"},
    ],
)
def test_automatic_mail_is_recognised(headers: dict[str, str]) -> None:
    email = parse_email(_raw(**headers))

    assert email is not None and email.automated


def test_an_email_without_a_sender_or_message_id() -> None:
    assert parse_email(b"Subject: hi\n\nno sender") is None
    email = parse_email(_raw(Message_ID=""))
    assert email is not None and email.message_id.startswith("<sha256-")


def test_the_reply_threads_under_the_customers_email() -> None:
    email = parse_email(_raw(References="<m0@example.org>"))
    assert email is not None

    reply = reply_message(email, "Spice Garden", "agentiq+spice@example.com", "**Yes**, halal.")

    assert reply["From"] == "Spice Garden <agentiq+spice@example.com>"
    assert reply["Subject"] == "Re: Halal food?"
    assert reply["References"] == "<m0@example.org> <m1@example.org>"
    assert reply["Message-ID"].endswith("@example.com>")
    assert reply.get_content().strip() == "Yes, halal."


# The IMAP mailbox


class FakeImap:
    """Answers like imaplib.IMAP4_SSL, recording what it was asked."""

    instances: ClassVar[list["FakeImap"]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.calls: list[tuple[Any, ...]] = [("connect", host, port)]
        self.folder_status = "OK"
        FakeImap.instances.append(self)

    def __enter__(self) -> "FakeImap":
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append(("logout",))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", user))

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return self.folder_status, [b"2"]

    def logout(self) -> None:
        self.calls.append(("logout",))

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7 9"]
        if command == "FETCH":
            return "OK", [(b"7 (UID 7 BODY[] {5}", b"raw-" + args[0]), b")"]
        return "OK", []


def test_the_mailbox_fetches_unread_mail_without_marking_it() -> None:
    FakeImap.instances.clear()
    mailbox = ImapMailbox("imap.test", 993, "me@x.test", "app-password", imap_class=FakeImap)

    assert mailbox.fetch_unseen() == [("7", b"raw-7"), ("9", b"raw-9")]
    mailbox.mark_seen(["7", "9"])

    fetch, mark = FakeImap.instances
    assert ("login", "me@x.test") in fetch.calls
    assert ("FETCH", b"7", "(BODY.PEEK[])") in fetch.calls  # PEEK: still unread
    assert ("STORE", "7,9", "+FLAGS", "(\\Seen)") in mark.calls
    assert fetch.calls[-1] == ("logout",)


def test_a_missing_folder_or_server_is_a_mailbox_error() -> None:
    class NoFolder(FakeImap):
        def select(self, folder: str) -> tuple[str, list[bytes]]:
            return "NO", [b"no such folder"]

    def unreachable(*_: object, **__: object) -> None:
        raise OSError("connection refused")

    with pytest.raises(MailboxError, match="Spam"):
        ImapMailbox("imap.test", 993, "u", "p", "Spam", imap_class=NoFolder).fetch_unseen()
    with pytest.raises(MailboxError, match="OSError") as caught:
        ImapMailbox("imap.test", 993, "u", "secret-pw", imap_class=unreachable).fetch_unseen()
    assert "secret-pw" not in str(caught.value)


# Text messages


def test_sms_replies_are_plain_escaped_and_short() -> None:
    assert message_twiml("Fish & chips <today>").endswith(
        "<Response><Message>Fish &amp; chips &lt;today&gt;</Message></Response>"
    )
    assert message_twiml(None).endswith("<Response></Response>")
    assert sms_text("**Booked!**\n- Ref ABC123") == "Booked!\nRef ABC123"
    long = sms_text("word " * 1000)
    assert len(long) <= SMS_MAX_CHARACTERS and long.endswith("…")


# Settings


def test_the_email_channel_needs_a_mailbox_and_a_mail_server() -> None:
    mailbox = Settings(
        _env_file=None, imap_host="imap.test", imap_username="u@x.test", imap_password="p"
    )

    assert not Settings(_env_file=None).email_inbound_enabled
    assert not mailbox.email_inbound_enabled
    assert mailbox.model_copy(update={"smtp_host": "smtp.test"}).email_inbound_enabled


# Serving the dashboard


async def test_the_dashboard_is_served_with_its_routes(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    mount_dashboard(app, tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        home = await client.get("/")
        deep_link = await client.get("/profiles/123/phone")
        asset = await client.get("/assets/app-1a2b.js")
        api = await client.get("/api/v1/health")
        missing_api = await client.get("/api/v1/nope")

    assert home.text == deep_link.text == "<div id=root></div>"
    assert deep_link.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"].endswith("immutable")
    assert api.json() == {"status": "ok"}
    assert missing_api.status_code == 404


def test_a_dashboard_folder_without_a_build_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="npm run build"):
        mount_dashboard(FastAPI(), tmp_path)
