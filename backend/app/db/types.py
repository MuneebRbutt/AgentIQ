"""Column helpers shared by all models."""

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

# Every timestamp is stored with its time zone (Postgres TIMESTAMPTZ) and handled in UTC.
Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True))]


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[Timestamp] = mapped_column(server_default=func.now())
