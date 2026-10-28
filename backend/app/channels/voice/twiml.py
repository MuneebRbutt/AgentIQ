"""TwiML, the XML Twilio follows during a call (https://www.twilio.com/docs/voice/twiml).

Only the verbs AgentIQ uses. Built as escaped strings rather than with an XML library:
the documents are tiny and write-only, and Twilio's SDK isn't worth a dependency for this
(CLAUDE.md §6).
"""

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

# A booking reference or other code with digits ("ABC123") is read letter by letter.
_CODE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,12}\b")
_MARKDOWN = re.compile(r"[*_#`>|]+")
_BULLET = re.compile(r"(?m)^\s*(?:[-•]|\d+[.)])\s+")
MAX_SPOKEN_CHARACTERS = 1500  # Twilio allows 4,096; anything longer is too long to listen to


def plain_text(text: str) -> str:
    """Reply text without markdown, line breaks kept, for channels that show or speak it
    as it is (calls, text messages)."""
    return _MARKDOWN.sub("", _BULLET.sub("", text)).strip()


def speakable(text: str) -> str:
    """Reply text as it should be spoken: no markdown, codes spelled out, not too long."""
    text = plain_text(text)
    text = _CODE.sub(lambda match: " ".join(match.group()), text)
    text = " ".join(text.split())
    if len(text) > MAX_SPOKEN_CHARACTERS:
        cut = text[:MAX_SPOKEN_CHARACTERS]
        text = cut[: cut.rfind(". ") + 1] or cut
    return text


def _attributes(values: dict[str, str | int | None]) -> str:
    return "".join(
        f" {key}={quoteattr(str(value))}" for key, value in values.items() if value is not None
    )


@dataclass(frozen=True, slots=True)
class Say:
    text: str
    voice: str | None = None
    language: str | None = None

    def xml(self) -> str:
        attributes = _attributes({"voice": self.voice, "language": self.language})
        return f"<Say{attributes}>{escape(self.text)}</Say>"


@dataclass(frozen=True, slots=True)
class Play:
    url: str

    def xml(self) -> str:
        return f"<Play>{escape(self.url)}</Play>"


Speech = Say | Play


@dataclass(frozen=True, slots=True)
class Gather:
    """Plays its prompt (the caller may talk over it) and sends what they said to
    `action` as `SpeechResult`. Silence falls through to the next verb."""

    action: str
    prompt: Speech | None
    language: str
    hints: str | None = None

    def xml(self) -> str:
        attributes = _attributes(
            {
                "input": "speech",
                "action": self.action,
                "method": "POST",
                "language": self.language,
                "speechTimeout": "auto",
                "speechModel": "phone_call",
                "enhanced": "true",
                "hints": self.hints,
            }
        )
        inner = self.prompt.xml() if self.prompt else ""
        return f"<Gather{attributes}>{inner}</Gather>"


@dataclass(frozen=True, slots=True)
class Record:
    """Records the caller until they pause, then sends `RecordingUrl` to `action`."""

    action: str
    max_seconds: int = 30
    silence_seconds: int = 3

    def xml(self) -> str:
        attributes = _attributes(
            {
                "action": self.action,
                "method": "POST",
                "maxLength": self.max_seconds,
                "timeout": self.silence_seconds,
                "playBeep": "false",
                "trim": "trim-silence",
            }
        )
        return f"<Record{attributes}/>"


@dataclass(frozen=True, slots=True)
class Redirect:
    url: str

    def xml(self) -> str:
        return f'<Redirect method="POST">{escape(self.url)}</Redirect>'


@dataclass(frozen=True, slots=True)
class Hangup:
    def xml(self) -> str:
        return "<Hangup/>"


Verb = Say | Play | Gather | Record | Redirect | Hangup


@dataclass
class VoiceResponse:
    verbs: list[Verb] = field(default_factory=list)

    def add(self, *verbs: Verb) -> "VoiceResponse":
        self.verbs.extend(verbs)
        return self

    def xml(self) -> str:
        body = "".join(verb.xml() for verb in self.verbs)
        return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
