"""Request and response shapes of the phone-number API (analysis M-5; ADR-0010)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PhoneNumberIn(BaseModel):
    number: str = Field(min_length=4, max_length=32, examples=["+14155550123"])


class PhoneNumberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str
    created_at: datetime


class PhoneSetupOut(BaseModel):
    """The profile's numbers and what to paste into the Twilio console for each."""

    numbers: list[PhoneNumberOut]
    # The number's "A call comes in" webhook (HTTP POST) and its status callback URL.
    incoming_webhook_url: str
    status_callback_url: str
    # The number's "A message comes in" webhook (HTTP POST), for text messages (ADR-0011).
    sms_webhook_url: str
    # False until TWILIO_AUTH_TOKEN is set: calls are refused while signatures can't be checked.
    signatures_checked: bool
