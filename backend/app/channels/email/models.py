"""Which email address reaches which business profile (FR-02-03; ADR-0011)."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class EmailAddress(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """An address customers write to, delivered to the shared mailbox (for example the
    plus address `agentiq+clinic@gmail.com`). One business per address."""

    __tablename__ = "email_addresses"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE"), index=True
    )
    address: Mapped[str] = mapped_column(String(320), unique=True)  # lower case
