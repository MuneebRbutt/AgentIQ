"""Request and response shapes of the email-address API (FR-02-03; ADR-0011)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EmailAddressIn(BaseModel):
    address: str = Field(min_length=3, max_length=320, examples=["agentiq+clinic@gmail.com"])


class EmailAddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    address: str
    created_at: datetime


class EmailSetupOut(BaseModel):
    """The profile's addresses and whether the server reads the mailbox they arrive in."""

    addresses: list[EmailAddressOut]
    # False until IMAP_* and SMTP_* are set: emails aren't read or answered yet.
    inbound_enabled: bool
    # The mailbox the server reads (IMAP_USERNAME). Its plus addresses
    # (name+clinic@gmail.com) all arrive there, one per business.
    mailbox_address: str | None
