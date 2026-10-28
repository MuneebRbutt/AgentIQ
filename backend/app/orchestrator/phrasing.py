"""Plain replies built from agent results, used when no language model is available or
the model fails (NFR04-01: the customer always gets an answer)."""

from app.agents.contracts import AgentResult, AgentStatus
from app.orchestrator.planning import NOTE_DECLINED
from app.profiles.schema import BusinessProfileConfig

_FIELD_WORDS = {
    "customer_name": "your name",
    "customer_phone": "your phone number",
    "customer_email": "your email address",
    "party_size": "the number of people",
    "service_id": "which service you'd like",
    "date": "the date",
    "time": "the time",
    "nights": "the number of nights",
    "reference": "your booking reference",
    "changes": "what you'd like to change",
}

NO_ANSWER = "Sorry, I don't have information about that."
SKIPPED = "I didn't go ahead with the rest, because the first part couldn't be done."
DECLINED = "No problem. What would you like to change?"
UNCLEAR = "Sorry, I didn't quite catch that. Could you say it another way?"
APOLOGY = "I'm sorry about that."
NOTE_UPSET = "customer_upset"  # the customer sounds unhappy (FR-06-02)


def template_reply(
    config: BusinessProfileConfig,
    results: list[tuple[str, AgentResult | None]],
    notes: list[str],
) -> str:
    parts = [_sentence(config, result) for _, result in results]
    if NOTE_DECLINED in notes:
        parts.append(DECLINED)
    if not parts:
        parts.append(f"How can I help you with {config.name} today?")
    if NOTE_UPSET in notes:
        parts.insert(0, APOLOGY)
    return " ".join(parts)


def _sentence(config: BusinessProfileConfig, result: AgentResult | None) -> str:
    if result is None:
        return SKIPPED
    if result.status is AgentStatus.NO_ANSWER:
        return NO_ANSWER
    if result.status is AgentStatus.NEEDS_INFO:
        return f"Could you tell me {_join([field_words(config, m) for m in result.missing])}?"
    if result.status is AgentStatus.NEEDS_CONFIRMATION:
        return f"{result.message} Shall I go ahead?"
    return result.message


def field_words(config: BusinessProfileConfig, field: str) -> str:
    if field.startswith("details."):
        key = field.removeprefix("details.")
        labels = {f.key: f.label for f in config.booking_rules.custom_fields}
        return f"the {labels.get(key, key).lower()}"
    return _FIELD_WORDS.get(field, field.replace("_", " "))


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
