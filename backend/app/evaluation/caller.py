"""The simulated customer for chat systems (AgentIQ and the single-agent baseline).

It says the scenario's scripted messages in order. When those run out and the system is
still asking, it answers the way a cooperative caller would: with their details when asked
for them, and "yes" to a read-back. The same rules drive every chat system (ADR-0009).
"""

import re
import time
from dataclasses import dataclass
from typing import Protocol

from app.evaluation.environment import TURN_SECONDS, ScenarioEnv, spoken_seconds
from app.evaluation.results import Transcript
from app.evaluation.scenarios import Caller, Scenario
from app.profiles.schema import BusinessProfileConfig

MAX_FOLLOW_UPS = 3
YES = "Yes, please go ahead."

# A reply that waits for the caller: a question, or a request such as "Please confirm: ...".
_QUESTION = re.compile(r"\?|(?i:\bplease (?:confirm|provide|tell|share|give|send)\b)")
_CONFIRM = re.compile(
    r"(?i)\b(?:confirm|shall i|should i|go ahead|would you like me to|do you want me to"
    r"|is (?:that|this|everything) (?:right|correct|ok|okay)|proceed)\b"
)
_DETAILS = re.compile(
    r"(?i)\b(?:your|the|a) (?:full )?(?:name|phone number|contact number|mobile number"
    r"|email(?: address)?)\b"
)


@dataclass(frozen=True, slots=True)
class ChatReply:
    text: str
    handed_off: bool = False


class ChatSession(Protocol):
    async def send(self, text: str) -> ChatReply: ...


def details_line(caller: Caller, config: BusinessProfileConfig) -> str:
    parts = [f"My name is {caller.name}", f"my phone number is {caller.phone}"]
    if caller.email:
        parts.append(f"my email is {caller.email}")
    labels = {field.key: field.label for field in config.booking_rules.custom_fields}
    parts += [
        f"{labels.get(key, key.replace('_', ' '))}: {value}"
        for key, value in caller.details.items()
    ]
    return ", ".join(parts) + "."


def asks_for_details(reply: str, config: BusinessProfileConfig) -> bool:
    """The reply asks for the caller's details, or for one of the profile's booking fields."""
    if _DETAILS.search(reply):
        return True
    lowered = reply.lower()
    return any(
        field.label.lower() in lowered or field.key.replace("_", " ") in lowered
        for field in config.booking_rules.custom_fields
    )


def follow_up(
    reply: str, caller: Caller, config: BusinessProfileConfig, gave_details: bool
) -> str | None:
    """What the caller says when the script has run out, or None to hang up."""
    if not _QUESTION.search(reply):
        return None
    if not gave_details and asks_for_details(reply, config):
        return details_line(caller, config)
    if _CONFIRM.search(reply):
        return YES
    return None


async def converse(
    session: ChatSession, scenario: Scenario, env: ScenarioEnv, transcript: Transcript
) -> None:
    greeting = env.config.assistant.greeting
    transcript.say("system", greeting)
    transcript.est_call_seconds += spoken_seconds(greeting)
    queue = env.turns(scenario)
    follow_ups = 0
    gave_details = False
    while queue:
        text = queue.pop(0)
        env.clock.advance(TURN_SECONDS)
        transcript.say("customer", text)
        started = time.perf_counter()
        try:
            reply = await session.send(text)
        except Exception as exc:  # a failing system is a result, not a crash of the run
            transcript.error = f"{type(exc).__name__}: {exc}"[:300]
            return
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        transcript.say("system", reply.text, latency_ms)
        transcript.est_call_seconds += (
            spoken_seconds(text) + latency_ms / 1000 + spoken_seconds(reply.text)
        )
        if reply.handed_off:
            transcript.handed_off = True
            return
        if not queue and follow_ups < MAX_FOLLOW_UPS:
            answer = follow_up(reply.text, scenario.caller, env.config, gave_details)
            if answer is not None:
                queue.append(answer)
                follow_ups += 1
                gave_details = gave_details or answer != YES
