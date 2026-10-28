"""The single-agent baseline (FR-10-05; analysis Q-5, ADR-0009).

One language model with one prompt does everything: it decides on its own when to search
the business's information, when to call the booking tool, when to hand off and what to
say. It uses the same model, the same retrieval and the same booking rules as AgentIQ (the
booking tool is the Booking agent itself), so the comparison isolates the architecture:
no separate understanding step, no parallel specialists, no sentiment model, and no code
that holds back a booking until the customer has said yes.
"""

import json
import re
from functools import cache
from pathlib import Path
from string import Template
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from app.agents.booking.agent import BookingAgent, BookingSubtask
from app.agents.contracts import AgentContext, AgentResult
from app.bookings.service import BookingService
from app.evaluation.caller import ChatReply, converse
from app.evaluation.environment import ScenarioEnv
from app.evaluation.results import Transcript
from app.evaluation.scenarios import Scenario
from app.knowledge.service import KnowledgeService
from app.profiles.schema import BookingMode
from app.providers.llm import ChatMessage, LLMClient, LLMError

MAX_STEPS = 5  # model calls per customer message
MAX_PASSAGES = 5
FALLBACK = "Sorry, I'm having trouble right now. Please call back in a few minutes."
_PROMPTS = Path(__file__).parent / "prompts"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@cache
def _template() -> Template:
    return Template((_PROMPTS / "single_agent.md").read_text(encoding="utf-8"))


class Step(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["search", "booking", "handoff", "reply"]
    query: str | None = None
    booking: dict[str, Any] | None = None
    reason: str | None = None
    text: str | None = None


def parse_step(raw: str) -> Step:
    step = Step.model_validate(json.loads(_FENCE.sub("", raw.strip())))
    if step.action == "reply" and not (step.text or "").strip():
        raise ValueError("a reply needs text")
    if step.action == "search" and not (step.query or "").strip():
        raise ValueError("a search needs a query")
    return step


def system_prompt(env: ScenarioEnv, caller_phone: str) -> str:
    config = env.config
    overnight = config.booking_rules.mode is BookingMode.OVERNIGHT
    now_local = env.clock.now().astimezone(ZoneInfo(config.timezone))
    extra = config.assistant.extra_instructions
    return _template().substitute(
        business_name=config.name,
        service_ids=", ".join(f'"{s.id}" ({s.name})' for s in config.services),
        date_rule="the check-in date" if overnight else "the date of the booking",
        time_rule=" (not used: bookings here are by night)" if overnight else "",
        nights_rule="" if overnight else " (not used here)",
        custom_fields=", ".join(
            f'"{f.key}" ({f.label})' for f in config.booking_rules.custom_fields
        )
        or "(none)",
        escalation_keywords=", ".join(config.escalation.keywords) or "(none)",
        caller_phone=caller_phone,
        tone=config.assistant.tone,
        extra_instructions=f" {extra}" if extra else "",
        today=now_local.date().isoformat(),
        weekday=now_local.strftime("%A"),
        timezone=config.timezone,
    )


class SingleAgentSystem:
    name = "single_agent"
    label = "Single-agent baseline"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def run(self, scenario: Scenario, env: ScenarioEnv) -> Transcript:
        transcript = Transcript()
        session = SingleAgentSession(self._llm, env, scenario.caller.phone)
        await converse(session, scenario, env, transcript)
        return transcript


class SingleAgentSession:
    def __init__(self, llm: LLMClient, env: ScenarioEnv, caller_phone: str) -> None:
        self._llm = llm
        self._env = env
        self._phone = caller_phone
        self._history: list[ChatMessage] = []

    async def send(self, text: str) -> ChatReply:
        self._history.append({"role": "user", "content": json.dumps({"customer": text})})
        for _ in range(MAX_STEPS):
            try:
                raw = await self._llm.complete(self._messages(), max_tokens=600, json_mode=True)
            except LLMError:
                return self._say(FALLBACK)
            self._history.append({"role": "assistant", "content": raw})
            try:
                step = parse_step(raw)
            except (ValueError, PydanticValidationError) as exc:
                self._result(
                    {"error": f"Not a valid step: {str(exc)[:200]}. Reply with one JSON object."}
                )
                continue
            if step.action == "reply":
                return ChatReply(text=(step.text or "").strip())
            if step.action == "handoff":
                return self._say(self._env.config.escalation.handoff_message, handed_off=True)
            if step.action == "search":
                self._result(await self._search(step.query or ""))
            else:
                self._result(await self._booking(step.booking or {}))
        return self._say(FALLBACK)

    def _messages(self) -> list[ChatMessage]:
        system: ChatMessage = {"role": "system", "content": system_prompt(self._env, self._phone)}
        return [system, *self._history]

    def _say(self, text: str, *, handed_off: bool = False) -> ChatReply:
        # Keep what the customer heard in the history, so the next turn has the context.
        self._history.append(
            {"role": "assistant", "content": json.dumps({"action": "reply", "text": text})}
        )
        return ChatReply(text=text, handed_off=handed_off)

    def _result(self, result: Any) -> None:
        self._history.append(
            {"role": "user", "content": json.dumps({"tool_result": result}, default=str)}
        )

    async def _search(self, query: str) -> list[dict[str, Any]]:
        shared = self._env.shared
        async with shared.session_factory() as db:
            knowledge = KnowledgeService(
                db, self._env.clock, shared.settings, shared.store, shared.embedder, shared.facts
            )
            passages = await knowledge.search(self._env.profile, query)
        return [{"text": p.text, "score": round(p.score, 2)} for p in passages[:MAX_PASSAGES]]

    async def _booking(self, fields: dict[str, Any]) -> dict[str, Any]:
        try:
            task = BookingSubtask.model_validate(
                {k: v for k, v in fields.items() if v not in (None, "", {})}
            )
        except PydanticValidationError as exc:
            return {"error": f"Invalid booking fields: {exc.errors(include_url=False)}"}
        context = AgentContext(
            tenant_id=self._env.tenant_id, profile=self._env.profile, customer_phone=self._phone
        )
        async with self._env.shared.session_factory() as db:
            result = await BookingAgent(BookingService(db, self._env.clock)).run(context, task)
        return _view(result)


def _view(result: AgentResult) -> dict[str, Any]:
    view: dict[str, Any] = {"status": result.status.value, "message": result.message}
    if result.missing:
        view["missing"] = result.missing
    if result.alternatives:
        view["alternatives"] = [alt.get("description", alt) for alt in result.alternatives]
    return view
