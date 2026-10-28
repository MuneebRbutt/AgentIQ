"""Response shapes of the follow-up API (FR-07, FR-08-03)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class FollowUpOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    kind: Literal["confirmation", "reminder", "feedback", "escalation"]
    booking_id: uuid.UUID | None
    session_id: uuid.UUID | None
    escalation_id: uuid.UUID | None
    channel: Literal["sms", "email"]
    recipient: str
    subject: str
    body: str
    due_at: datetime
    status: Literal["pending", "sent", "logged", "failed", "cancelled"]
    attempts: int
    last_error: str | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _session_from_interaction(cls, value: object) -> object:
        # The table says interaction; the API says session, like the sessions endpoints.
        if hasattr(value, "interaction_id"):
            return {
                name: getattr(value, "interaction_id" if name == "session_id" else name)
                for name in cls.model_fields
            }
        return value
