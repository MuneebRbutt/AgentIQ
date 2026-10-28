"""Tenant ownership and query scoping (NFR03-02).

Rules:
- Every model holding a business's data inherits `TenantOwnedMixin`.
- Every query for such data goes through `scoped()` with the tenant ID from the
  authenticated context (`CurrentAuth`), never from request bodies or model output.
- A row belonging to another tenant is reported as "not found", so its existence isn't
  revealed either.
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Select
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


class TenantOwnedMixin:
    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)


def scoped[S: Select[Any]](statement: S, model: type[TenantOwnedMixin], tenant_id: uuid.UUID) -> S:
    """Restrict a SELECT to one tenant's rows."""
    return statement.where(model.tenant_id == tenant_id)
