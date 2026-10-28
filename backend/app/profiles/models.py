"""Stored business profiles and their saved versions (FR-08-04/06, FR-09-02)."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.scoping import TenantOwnedMixin


class BusinessProfile(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """One business line an operator configures. The settings live in `config` (ADR-0002).

    `name` and `domain` are copies of the config values, kept for listing and filtering.
    """

    __tablename__ = "business_profiles"

    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Increases on every save; an edit must name the version it started from.
    version: Mapped[int]
    updated_at: Mapped[Timestamp] = mapped_column(server_default=func.now())


class BusinessProfileVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A snapshot of every saved version, so changes can be reviewed and rolled back."""

    __tablename__ = "business_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version"),)

    # Indexed through the (profile_id, version) unique constraint.
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("business_profiles.id", ondelete="CASCADE")
    )
    version: Mapped[int]
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    saved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operators.id", ondelete="SET NULL")
    )
