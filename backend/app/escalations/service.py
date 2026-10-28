"""Recording, notifying and handling escalations (FR-06-04, FR-08-03; analysis Q-6)."""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import AgentResult
from app.conversations.models import Interaction
from app.core.clock import Clock
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.escalations.models import Escalation
from app.followups.service import escalation_retry
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig
from app.providers.notifications import Notice, Notifier
from app.tenancy.scoping import scoped

log = get_logger(__name__)

ESCALATION_NOT_FOUND = "Escalation not found."
NOTIFY_TIMEOUT_SECONDS = 10.0
MESSAGE_LIMIT = 500

Status = Literal["open", "acknowledged", "resolved"]

_REASON_WORDS = {
    "negative_sentiment": "the customer is very unhappy",
    "keyword": "the customer used a word on your escalation list",
    "repeated_negative": "the customer has been unhappy for several messages",
    "unclear": "the assistant couldn't understand the customer",
}


def new_escalation(
    interaction: Interaction,
    turn_seq: int,
    reasons: list[str],
    customer_message: str,
    sentiment: AgentResult | None,
    config: BusinessProfileConfig,
    now: datetime,
) -> Escalation:
    rules = config.escalation
    has_contact = bool(rules.notify_email or rules.notify_phone)
    return Escalation(
        id=uuid.uuid4(),
        tenant_id=interaction.tenant_id,
        profile_id=interaction.profile_id,
        interaction_id=interaction.id,
        turn_seq=turn_seq,
        reasons=reasons,
        customer_message=customer_message[:MESSAGE_LIMIT],
        customer_phone=interaction.customer_phone,
        sentiment_label=sentiment.data.get("label") if sentiment else None,
        sentiment_confidence=sentiment.data.get("confidence") if sentiment else None,
        status="open",
        # Sending happens after the turn is saved; "failed" until it succeeds.
        notification="failed" if has_contact else "none",
        created_at=now,
        updated_at=now,
    )


def notice_for(escalation: Escalation, config: BusinessProfileConfig) -> Notice:
    why = "; ".join(_REASON_WORDS.get(reason, reason) for reason in escalation.reasons)
    caller = escalation.customer_phone or "unknown number"
    return Notice(
        subject=f"{config.name}: a customer needs a call back",
        body=(
            f"AgentIQ handed a conversation to your team because {why}. "
            f'Customer: {caller}. They said: "{escalation.customer_message[:300]}"'
        ),
        email=config.escalation.notify_email,
        phone=config.escalation.notify_phone,
    )


class EscalationService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def notify(
        self,
        escalation_id: uuid.UUID,
        config: BusinessProfileConfig,
        notifier: Notifier,
        *,
        retry_after: timedelta | None = None,
    ) -> None:
        """Tell the business's contact, after the turn is saved, so a slow or failing
        notifier never delays or loses the customer's reply (NFR04-01). If it fails, the
        Follow-Up worker tries once more after `retry_after` (UC-07, ADR-0007)."""
        escalation = await self._db.get(Escalation, escalation_id)
        if escalation is None or escalation.notification == "none":
            return
        notice = notice_for(escalation, config)
        try:
            delivery = await asyncio.wait_for(notifier.send(notice), NOTIFY_TIMEOUT_SECONDS)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            log.warning(
                "escalation_notify_failed",
                escalation_id=str(escalation.id),
                error=type(exc).__name__,
                retry=retry_after is not None,
            )
            if retry_after is not None:
                self._db.add(
                    escalation_retry(escalation, notice, error, self._clock.now(), retry_after)
                )
                await self._db.commit()
            return
        escalation.notification = delivery
        escalation.notified_at = self._clock.now()
        await self._db.commit()

    async def list_recent(
        self,
        profile: BusinessProfile,
        *,
        status: Status | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Escalation]:
        statement = (
            scoped(select(Escalation), Escalation, profile.tenant_id)
            .where(Escalation.profile_id == profile.id)
            .order_by(Escalation.created_at.desc(), Escalation.id)
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(Escalation.status == status)
        if session_id is not None:
            statement = statement.where(Escalation.interaction_id == session_id)
        return list(await self._db.scalars(statement))

    async def get(self, profile: BusinessProfile, escalation_id: uuid.UUID) -> Escalation:
        statement = scoped(select(Escalation), Escalation, profile.tenant_id).where(
            Escalation.id == escalation_id, Escalation.profile_id == profile.id
        )
        escalation = await self._db.scalar(statement)
        if escalation is None:
            raise NotFoundError(ESCALATION_NOT_FOUND)
        return escalation

    async def update(
        self,
        profile: BusinessProfile,
        escalation_id: uuid.UUID,
        operator_id: uuid.UUID,
        *,
        status: Status | None,
        note: str | None,
    ) -> Escalation:
        escalation = await self.get(profile, escalation_id)
        if status is not None:
            escalation.status = status
        if note is not None:
            escalation.note = note
        escalation.handled_by = operator_id
        escalation.updated_at = self._clock.now()
        await self._db.commit()
        return escalation
