"""What the understand step returns: the customer's request as typed subtasks (FR-03-01/02).

The model answers in JSON. Everything it says is validated here before any agent sees it,
and fields it may not set (the booking's `confirmed` flag, tenant or profile IDs) are
dropped: those come from code and the channel, never from the model (CLAUDE.md rules 1, 3).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic import ValidationError as PydanticValidationError

from app.agents.booking.agent import BookingSubtask
from app.agents.knowledge.agent import KnowledgeSubtask

MAX_SUBTASKS = 6
_BOOKING_FIELDS = set(BookingSubtask.model_fields) - {"confirmed"}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class UnderstandingError(Exception):
    """The model's output isn't a usable understanding."""


class SubtaskSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    agent: Literal["booking", "knowledge"]
    question: Annotated[str, StringConstraints(max_length=1000)] | None = None
    booking: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_SUBTASKS)


class Understanding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subtasks: list[SubtaskSpec] = Field(default_factory=list, max_length=MAX_SUBTASKS)
    # The customer's answer to a read-back that was awaiting confirmation, if any.
    confirmation: Literal["yes", "no"] | None = None
    clarification_question: Annotated[str, StringConstraints(max_length=500)] | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


@dataclass(frozen=True, slots=True)
class Task:
    """A validated subtask, ready to hand to an agent."""

    id: str
    agent: Literal["booking", "knowledge"]
    knowledge: KnowledgeSubtask | None = None
    booking: BookingSubtask | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Parsed:
    understanding: Understanding
    tasks: list[Task]


def parse_understanding(raw: str) -> Parsed:
    try:
        payload = json.loads(_FENCE.sub("", raw.strip()))
        understanding = Understanding.model_validate(payload)
    except (json.JSONDecodeError, PydanticValidationError) as exc:
        raise UnderstandingError(f"not a valid understanding: {exc}") from exc
    return Parsed(understanding, [_task(spec) for spec in understanding.subtasks])


def _task(spec: SubtaskSpec) -> Task:
    try:
        if spec.agent == "knowledge":
            question = KnowledgeSubtask(question=spec.question or "")
            return Task(spec.id, "knowledge", knowledge=question, depends_on=tuple(spec.depends_on))
        fields = {k: v for k, v in (spec.booking or {}).items() if k in _BOOKING_FIELDS}
        fields = {k: v for k, v in fields.items() if v not in (None, "", {})}
        booking = BookingSubtask.model_validate(fields)
    except PydanticValidationError as exc:
        raise UnderstandingError(f"subtask {spec.id} is invalid: {exc}") from exc
    return Task(spec.id, "booking", booking=booking, depends_on=tuple(spec.depends_on))
