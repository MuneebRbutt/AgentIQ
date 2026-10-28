"""Follow-up messages for the operator: list, read, cancel (FR-07, FR-08-03; ADR-0007).

The worker (`app/workers/followups.py`) plans and sends them; this module holds what both
share: building a queued row, and the tenant-scoped reads the dashboard needs.
"""

import uuid
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.followup.agent import PlannedMessage
from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError
from app.escalations.models import Escalation
from app.followups.models import FollowUpTask
from app.profiles.models import BusinessProfile
from app.providers.notifications import Notice
from app.tenancy.scoping import scoped

FOLLOW_UP_NOT_FOUND = "Follow-up not found."
ONLY_PENDING = "Only a follow-up that hasn't been sent can be cancelled."
# The first try plus one retry (UC-07: "logs the failure and retries once").
MAX_ATTEMPTS = 2

Kind = Literal["confirmation", "reminder", "feedback", "escalation"]
Status = Literal["pending", "sent", "logged", "failed", "cancelled"]


def new_task(
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    planned: PlannedMessage,
    now: datetime,
    *,
    booking_id: uuid.UUID | None = None,
    interaction_id: uuid.UUID | None = None,
) -> FollowUpTask:
    return FollowUpTask(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        profile_id=profile_id,
        kind=planned.kind,
        booking_id=booking_id,
        interaction_id=interaction_id,
        channel=planned.channel,
        recipient=planned.recipient,
        subject=planned.subject[:200],
        body=planned.body,
        due_at=planned.due_at,
        status="pending",
        attempts=0,
        created_at=now,
        updated_at=now,
    )


def escalation_retry(
    escalation: Escalation, notice: Notice, error: str, now: datetime, retry_after: timedelta
) -> FollowUpTask:
    """The business's escalation notice failed: queue the one retry (UC-07)."""
    channel: Literal["sms", "email"] = "sms" if notice.phone else "email"
    return FollowUpTask(
        id=uuid.uuid4(),
        tenant_id=escalation.tenant_id,
        profile_id=escalation.profile_id,
        kind="escalation",
        interaction_id=escalation.interaction_id,
        escalation_id=escalation.id,
        channel=channel,
        recipient=notice.phone or notice.email or "",
        subject=notice.subject[:200],
        body=notice.body,
        due_at=now + retry_after,
        status="pending",
        attempts=1,  # the immediate try already happened
        last_error=error[:300],
        created_at=now,
        updated_at=now,
    )


async def cancel_pending_for_booking(
    db: AsyncSession, tenant_id: uuid.UUID, booking_id: uuid.UUID, now: datetime
) -> int:
    """A booking changed or was cancelled: unsent confirmations and reminders are stale."""
    result = await db.execute(
        update(FollowUpTask)
        .where(
            FollowUpTask.tenant_id == tenant_id,
            FollowUpTask.booking_id == booking_id,
            FollowUpTask.status == "pending",
            FollowUpTask.kind.in_(("confirmation", "reminder")),
        )
        .values(status="cancelled", updated_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


class FollowUpService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def list_recent(
        self,
        profile: BusinessProfile,
        *,
        status: Status | None = None,
        kind: Kind | None = None,
        booking_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FollowUpTask]:
        """Latest due first, so upcoming reminders sit above what was already sent."""
        statement = (
            scoped(select(FollowUpTask), FollowUpTask, profile.tenant_id)
            .where(FollowUpTask.profile_id == profile.id)
            .order_by(FollowUpTask.due_at.desc(), FollowUpTask.id)
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(FollowUpTask.status == status)
        if kind is not None:
            statement = statement.where(FollowUpTask.kind == kind)
        if booking_id is not None:
            statement = statement.where(FollowUpTask.booking_id == booking_id)
        if session_id is not None:
            statement = statement.where(FollowUpTask.interaction_id == session_id)
        return list(await self._db.scalars(statement))

    async def get(
        self, profile: BusinessProfile, follow_up_id: uuid.UUID, *, lock: bool = False
    ) -> FollowUpTask:
        statement = scoped(select(FollowUpTask), FollowUpTask, profile.tenant_id).where(
            FollowUpTask.id == follow_up_id, FollowUpTask.profile_id == profile.id
        )
        if lock:
            statement = statement.with_for_update()
        task = await self._db.scalar(statement)
        if task is None:
            raise NotFoundError(FOLLOW_UP_NOT_FOUND)
        return task

    async def cancel(self, profile: BusinessProfile, follow_up_id: uuid.UUID) -> FollowUpTask:
        """The operator stops a message that hasn't gone out yet (e.g. a reminder)."""
        # Locked, so a message the worker is sending right now is seen as sent, not cancelled.
        task = await self.get(profile, follow_up_id, lock=True)
        if task.status == "cancelled":
            return task
        if task.status != "pending":
            raise ConflictError(ONLY_PENDING)
        task.status = "cancelled"
        task.updated_at = self._clock.now()
        await self._db.commit()
        return task
