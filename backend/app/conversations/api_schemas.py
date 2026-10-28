"""Request and response shapes of the conversation API (test console, FR-08-02/03)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agents.contracts import AgentResult
from app.escalations.api_schemas import EscalationOut


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The caller's number as a phone line would give it, to test bookings by phone.
    customer_phone: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)] | None
    ) = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    channel: str
    customer_phone: str | None
    customer_email: str | None  # the sender, for conversations by email
    status: Literal["open", "handed_off", "closed"]
    turn_count: int
    needs_review: bool  # an uncertain sentiment reading asks a person to look (UC-06)
    created_at: datetime
    updated_at: datetime


class StartedSessionOut(SessionOut):
    greeting: str  # the profile's opening line, to show before the first message


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    role: Literal["customer", "assistant"]
    text: str
    # Assistant turns: outcome, understanding, agent results, skipped tasks and timings.
    data: dict[str, Any]
    created_at: datetime


class SentimentOut(BaseModel):
    """How one customer message sounded (FR-06-03/05)."""

    model_config = ConfigDict(from_attributes=True)

    turn_seq: int
    label: Literal["negative", "neutral", "positive"]
    confidence: float
    scores: dict[str, float]
    model: str
    low_confidence: bool
    complaint: bool
    reasons: list[str]


class SessionDetail(SessionOut):
    turns: list[TurnOut]
    sentiment: list[SentimentOut]  # one per customer message, in order
    escalations: list[EscalationOut]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class MessageOut(BaseModel):
    session_id: uuid.UUID
    reply: str
    outcome: Literal["answered", "clarify", "handoff"]
    handed_off: bool
    results: list[AgentResult]
    # How the customer's message sounded and what the escalation rules decided (FR-06).
    sentiment: AgentResult | None = None
    escalation_id: uuid.UUID | None = None
    # Milliseconds per stage: understand, agent.sentiment, agent.<name>.<task>, reply,
    # total (FR-10-02).
    timings: dict[str, float] = Field(default_factory=dict)
