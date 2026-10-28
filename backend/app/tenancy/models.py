"""Tenants: one per SMB customer of AgentIQ. Every business record belongs to exactly one."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import CreatedAtMixin, UUIDPrimaryKeyMixin


class Tenant(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
