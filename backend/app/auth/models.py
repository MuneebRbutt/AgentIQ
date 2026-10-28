"""Operator accounts and their login sessions (FR-01)."""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import CreatedAtMixin, Timestamp, UUIDPrimaryKeyMixin
from app.tenancy.models import Tenant
from app.tenancy.scoping import TenantOwnedMixin


class Operator(UUIDPrimaryKeyMixin, TenantOwnedMixin, CreatedAtMixin, Base):
    """A person who manages one tenant's business profiles through the dashboard."""

    __tablename__ = "operators"
    __table_args__ = (
        # Emails are stored lower-cased; one account per email across all tenants, because
        # login doesn't ask which business you belong to.
        Index("uq_operators_email", "email", unique=True),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    email: Mapped[str] = mapped_column(String(320))
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    failed_login_attempts: Mapped[int] = mapped_column(server_default=text("0"))
    locked_until: Mapped[Timestamp | None]
    last_login_at: Mapped[Timestamp | None]

    tenant: Mapped[Tenant] = relationship(lazy="raise")


class OperatorSession(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Server-side record behind each access token. Revoking it ends the session (FR-01-04)."""

    __tablename__ = "operator_sessions"

    operator_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("operators.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[Timestamp]
    revoked_at: Mapped[Timestamp | None]
    last_seen_at: Mapped[Timestamp] = mapped_column(server_default=func.now())

    operator: Mapped[Operator] = relationship(lazy="raise")
