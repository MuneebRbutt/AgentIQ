"""Building the understand and reply prompts from the profile and the conversation."""

import json
from datetime import datetime
from functools import cache
from pathlib import Path
from string import Template
from typing import Any

from app.agents.contracts import AgentResult
from app.profiles.schema import BookingMode, BusinessProfileConfig
from app.providers.llm import ChatMessage

_PROMPTS = Path(__file__).parent / "prompts"


@cache
def _template(name: str) -> Template:
    return Template((_PROMPTS / f"{name}.md").read_text(encoding="utf-8"))


def understand_messages(
    config: BusinessProfileConfig,
    now_local: datetime,
    history: list[dict[str, str]],
    draft: dict[str, Any] | None,
    awaiting_confirmation: bool,
    text: str,
) -> list[ChatMessage]:
    overnight = config.booking_rules.mode is BookingMode.OVERNIGHT
    services = ", ".join(f'"{s.id}" ({s.name})' for s in config.services)
    fields = ", ".join(f'"{f.key}" ({f.label})' for f in config.booking_rules.custom_fields)
    system = _template("understand").substitute(
        business_name=config.name,
        service_ids=services,
        date_rule="the check-in date" if overnight else "the date of the booking",
        time_rule=" (not used: bookings here are by night)" if overnight else "",
        nights_rule="" if overnight else " (not used here)",
        custom_fields=fields or "(none)",
        today=now_local.date().isoformat(),
        weekday=now_local.strftime("%A"),
        timezone=config.timezone,
    )
    context = {
        "conversation": history,
        "booking_in_progress": draft,
        "awaiting_confirmation": awaiting_confirmation,
        "message": text,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
    ]


def reply_messages(
    config: BusinessProfileConfig,
    text: str,
    results: list[tuple[str, AgentResult | None]],
    notes: list[str],
) -> list[ChatMessage]:
    extra = config.assistant.extra_instructions
    system = _template("reply").substitute(
        business_name=config.name,
        tone=config.assistant.tone,
        extra_instructions=f"\n{extra}" if extra else "",
    )
    payload = {
        "customer_message": text,
        "results": [_result_view(result) for _, result in results],
        "notes": notes,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def _result_view(result: AgentResult | None) -> dict[str, Any]:
    if result is None:
        return {"status": "skipped"}
    view: dict[str, Any] = {"agent": result.agent, "status": result.status.value}
    view["message"] = result.message
    if result.missing:
        view["missing"] = result.missing
    if result.alternatives:
        view["alternatives"] = [alt.get("description", alt) for alt in result.alternatives]
    return view
