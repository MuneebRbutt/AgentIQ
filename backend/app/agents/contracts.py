"""What every specialist agent receives and returns (NFR05-01; analysis D-8, ADR-0003).

The orchestrator (M5) turns the customer's words into a typed subtask, calls the agent,
and phrases the reply from the `AgentResult`. Agents never write the final reply text
themselves and never see another tenant's data: the context fixes the tenant and profile.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.profiles.models import BusinessProfile


class AgentStatus(StrEnum):
    COMPLETED = "completed"  # done; `message` and `data` describe the outcome
    NEEDS_INFO = "needs_info"  # ask the customer for the fields in `missing`
    NEEDS_CONFIRMATION = "needs_confirmation"  # read `message` back and ask to confirm
    UNAVAILABLE = "unavailable"  # offer `alternatives`
    REJECTED = "rejected"  # a rule or lookup prevents it; `message` says why
    # The business's knowledge doesn't cover the question; say so rather than guess (FR-05-05).
    NO_ANSWER = "no_answer"


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Facts the agent may trust. They come from the channel, never from the model."""

    tenant_id: uuid.UUID
    profile: BusinessProfile
    # The caller's number from the phone line or SMS sender, when known (analysis AS-5).
    customer_phone: str | None = None


class AgentResult(BaseModel):
    agent: str
    status: AgentStatus
    message: str  # a plain, factual sentence for the orchestrator to phrase
    missing: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
