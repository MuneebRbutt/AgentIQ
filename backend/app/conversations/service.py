"""Conversations seen by the operator: start, list, read and close (FR-08-02/03, FR-09-04).

The orchestrator (`app/orchestrator/service.py`) handles the messages; this service only
manages the conversation records around them, always inside the caller's tenant.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.models import Interaction, SentimentRecord, Turn
from app.core.clock import Clock
from app.core.errors import NotFoundError
from app.profiles.models import BusinessProfile
from app.tenancy.scoping import scoped

CONVERSATION_NOT_FOUND = "Conversation not found."

Channel = Literal["text", "sms", "voice", "email"]
Status = Literal["open", "handed_off", "closed"]


def new_interaction(
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    channel: Channel,
    customer_phone: str | None,
    now: datetime,
    *,
    customer_email: str | None = None,
) -> Interaction:
    return Interaction(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        profile_id=profile_id,
        channel=channel,
        customer_phone=customer_phone,
        customer_email=customer_email,
        status="open",
        memory={},
        turn_count=0,
        created_at=now,
        updated_at=now,
    )


class ConversationService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def start(
        self, profile: BusinessProfile, channel: Channel, customer_phone: str | None
    ) -> Interaction:
        interaction = new_interaction(
            profile.tenant_id, profile.id, channel, customer_phone, self._clock.now()
        )
        self._db.add(interaction)
        await self._db.commit()
        return interaction

    async def list_recent(
        self,
        profile: BusinessProfile,
        *,
        status: Status | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Interaction]:
        """Most recently active first, for the operator's logs (FR-08-03)."""
        statement = (
            scoped(select(Interaction), Interaction, profile.tenant_id)
            .where(Interaction.profile_id == profile.id)
            .order_by(Interaction.updated_at.desc(), Interaction.id)
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            statement = statement.where(Interaction.status == status)
        return list(await self._db.scalars(statement))

    async def get(self, profile: BusinessProfile, session_id: uuid.UUID) -> Interaction:
        statement = scoped(select(Interaction), Interaction, profile.tenant_id).where(
            Interaction.id == session_id, Interaction.profile_id == profile.id
        )
        interaction = await self._db.scalar(statement)
        if interaction is None:
            raise NotFoundError(CONVERSATION_NOT_FOUND)
        return interaction

    async def turns(self, interaction: Interaction) -> list[Turn]:
        statement = (
            scoped(select(Turn), Turn, interaction.tenant_id)
            .where(Turn.interaction_id == interaction.id)
            .order_by(Turn.seq)
        )
        return list(await self._db.scalars(statement))

    async def sentiment(self, interaction: Interaction) -> list[SentimentRecord]:
        """How each customer message sounded, in order (FR-06-05)."""
        statement = (
            scoped(select(SentimentRecord), SentimentRecord, interaction.tenant_id)
            .where(SentimentRecord.interaction_id == interaction.id)
            .order_by(SentimentRecord.turn_seq)
        )
        return list(await self._db.scalars(statement))

    async def close(self, profile: BusinessProfile, session_id: uuid.UUID) -> Interaction:
        """End the conversation; further messages are refused. Closing twice is harmless."""
        interaction = await self.get(profile, session_id)
        if interaction.status != "closed":
            interaction.status = "closed"
            interaction.updated_at = self._clock.now()
            await self._db.commit()
        return interaction
