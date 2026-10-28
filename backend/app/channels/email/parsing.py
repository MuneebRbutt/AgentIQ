"""Reading a customer's email: who sent it, to which address, and what they wrote
(FR-02-03: subject and body; ADR-0011).

Only the customer's new words go to the assistant: quoted earlier messages and signatures
are cut, and an HTML-only email is reduced to its text. Automatic mail (out-of-office
replies, bounces, newsletters) is recognised so it is never answered, which would start a
loop between two robots (RFC 3834).
"""

import hashlib
import re
from dataclasses import dataclass
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser

_REPLY_PREFIX = re.compile(r"^\s*(?:re|aw|fw|fwd)\s*:\s*", re.IGNORECASE)
# Where a reply's quoted history starts (Gmail, Apple Mail, Outlook).
_QUOTE_STARTS = (
    re.compile(r"(?m)^\s*On\b[^\n]*(?:\n[^\n]*)?\bwrote:\s*$"),
    re.compile(r"(?m)^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"(?m)^_{10,}\s*$"),
    re.compile(r"(?m)^\s*From:\s.+\n\s*(?:Sent|Date):\s", re.IGNORECASE),
    re.compile(r"(?m)^-- ?$"),  # signature delimiter
)
_AUTOMATED_SENDERS = frozenset(
    {"mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply", "do-not-reply"}
)
_RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To")


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    message_id: str
    sender: str  # lower case
    recipients: list[str]  # lower case, in header order, no duplicates
    subject: str
    body: str
    in_reply_to: str | None
    references: str | None
    automated: bool

    @property
    def is_reply(self) -> bool:
        return bool(self.in_reply_to) or bool(_REPLY_PREFIX.match(self.subject))

    @property
    def text(self) -> str:
        """What the customer asked: the body, with the subject first when it starts a
        conversation (a reply's "Re: ..." adds nothing)."""
        subject = _REPLY_PREFIX.sub("", self.subject).strip()
        if self.is_reply or not subject or subject.lower() in self.body.lower():
            return self.body or subject
        return f"{subject}\n\n{self.body}".strip()


class _TextExtractor(HTMLParser):
    _BLOCKS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self._skip += 1
        elif tag == "blockquote":
            self._skip += 1  # quoted history
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "blockquote"}:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return "".join(extractor.parts)


def strip_quoted(text: str) -> str:
    """The new part of a reply: before the quoted history and signature, without `>`
    lines, with blank runs collapsed."""
    text = text.replace("\r\n", "\n")
    cut = min((m.start() for p in _QUOTE_STARTS if (m := p.search(text))), default=len(text))
    lines = [line.rstrip() for line in text[:cut].split("\n") if not line.startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):  # an unknown or broken charset
        payload = part.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else ""
    if not isinstance(content, str):
        return ""
    if part.get_content_type() == "text/html":
        content = html_to_text(content)
    return strip_quoted(content)


def _automated(message: EmailMessage, sender: str) -> bool:
    auto_submitted = str(message.get("Auto-Submitted", "no")).strip().lower()
    precedence = str(message.get("Precedence", "")).strip().lower()
    local_part = sender.partition("@")[0]
    return (
        auto_submitted != "no"
        or precedence in {"bulk", "junk", "list", "auto_reply"}
        or any(message.get(h) for h in ("List-Id", "List-Unsubscribe", "X-Autoreply"))
        or message.get_content_type() == "multipart/report"  # bounces
        or local_part in _AUTOMATED_SENDERS
    )


def parse_email(raw: bytes) -> ParsedEmail | None:
    """None when the message has no usable sender."""
    message = message_from_bytes(raw, policy=policy.default)
    if not isinstance(message, EmailMessage):  # pragma: no cover  # policy.default ensures it
        return None
    sender = parseaddr(str(message.get("From", "")))[1].strip().lower()
    if "@" not in sender:
        return None
    recipients: list[str] = []
    for header in _RECIPIENT_HEADERS:
        for _, address in getaddresses([str(v) for v in message.get_all(header, [])]):
            address = address.strip().lower()
            if "@" in address and address not in recipients:
                recipients.append(address)
    message_id = str(message.get("Message-ID", "")).strip()
    return ParsedEmail(
        message_id=message_id or f"<sha256-{hashlib.sha256(raw).hexdigest()}>",
        sender=sender,
        recipients=recipients,
        subject=" ".join(str(message.get("Subject", "")).split()),
        body=_body(message),
        in_reply_to=str(message.get("In-Reply-To", "")).strip() or None,
        references=" ".join(str(message.get("References", "")).split()) or None,
        automated=_automated(message, sender),
    )
