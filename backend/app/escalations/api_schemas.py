"""Request and response shapes of the escalation API (FR-06-04, FR-08-03)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator


class EscalationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    session_id: uuid.UUID
    turn_seq: int
    reasons: list[str]
    customer_message: str
    customer_phone: str | None
    sentiment_label: str | None
    sentiment_confidence: float | None
    status: Literal["open", "acknowledged", "resolved"]
    notification: Literal["none", "logged", "sent", "failed"]
    notified_at: datetime | None
    note: str | None
    handled_by: uuid.UUID | None
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


class EscalationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["open", "acknowledged", "resolved"] | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> "EscalationUpdate":
        if self.status is None and self.note is None:
            raise ValueError("send a status, a note or both")
        return self
