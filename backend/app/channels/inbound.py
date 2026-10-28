"""Messages customers write to a business, by text message or email (FR-02-02/03; ADR-0011).

Both channels work the same way once the business is known (from the number or address the
message was sent to, never from its content, NFR03-02):

1. the message is recorded once, by the provider's id, so a retried webhook or a mailbox
   read twice is not answered twice;
2. a sender who has written more than `INBOUND_MAX_MESSAGES_PER_HOUR` times in the last
   hour gets no answer (analysis M-15);
3. the message continues the sender's open conversation on that channel, or starts one;
4. the orchestrator answers it like any other channel.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.models import InboundMessage
from app.conversations.models import Interaction
from app.conversations.service import new_interaction
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.orchestrator.service import NormalizedRequest, Orchestrator
from app.providers.notifications import mask_contact

log = get_logger(__name__)

InboundChannel = Literal["sms", "email"]

MAX_MESSAGE_CHARACTERS = 2000  # what the orchestrator accepts
ENDED = "Thanks, our team has your message and will get back to you."


@dataclass(frozen=True, slots=True)
class Inbound:
    tenant_id: uuid.UUID
    profile_id: uuid.UUID
    channel: InboundChannel
    external_id: str
    sender: str  # E.164 for sms, lower-case address for email
    text: str


@dataclass(frozen=True, slots=True)
class InboundResult:
    # reply: answer with `reply`. duplicate: already answered. limited: too many messages.
    kind: Literal["reply", "duplicate", "limited"]
    reply: str | None = None
    session_id: uuid.UUID | None = None
    handed_off: bool = False


class InboundHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        orchestrator: Orchestrator,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._orchestrator = orchestrator

    async def handle(self, message: Inbound) -> InboundResult:
        async with self._session_factory() as db:
            record = InboundMessage(
                tenant_id=message.tenant_id,
                profile_id=message.profile_id,
                channel=message.channel,
                external_id=message.external_id[:255],
                sender=message.sender,
                created_at=self._clock.now(),
            )
            db.add(record)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return InboundResult("duplicate")
            if await self._over_limit(db, message):
                log.warning(
                    "inbound_rate_limited",
                    channel=message.channel,
                    sender=mask_contact(message.sender),
                    profile_id=str(message.profile_id),
                )
                return InboundResult("limited")
            interaction = await self._conversation(db, message)
            record.interaction_id = interaction.id
            await db.commit()
            session_id = interaction.id

        try:
            outcome = await self._orchestrator.handle(
                NormalizedRequest(
                    tenant_id=message.tenant_id,
                    profile_id=message.profile_id,
                    session_id=session_id,
                    channel=message.channel,
                    text=message.text[:MAX_MESSAGE_CHARACTERS],
                )
            )
        except ConflictError:  # handed over or closed between the lookup and the turn
            return InboundResult("reply", ENDED, session_id)
        return InboundResult("reply", outcome.reply, session_id, outcome.handed_off)

    async def _over_limit(self, db: AsyncSession, message: Inbound) -> bool:
        since = self._clock.now() - timedelta(hours=1)
        count = await db.scalar(
            select(func.count())
            .select_from(InboundMessage)
            .where(
                InboundMessage.profile_id == message.profile_id,
                InboundMessage.sender == message.sender,
                InboundMessage.created_at >= since,
            )
        )
        return int(count or 0) > self._settings.inbound_max_messages_per_hour

    async def _conversation(self, db: AsyncSession, message: Inbound) -> Interaction:
        """The sender's open conversation on this channel, unless it has gone quiet for
        longer than a conversation lasts (M-14); otherwise a new one."""
        contact = (
            Interaction.customer_phone if message.channel == "sms" else Interaction.customer_email
        )
        statement = (
            select(Interaction)
            .where(
                Interaction.tenant_id == message.tenant_id,
                Interaction.profile_id == message.profile_id,
                Interaction.channel == message.channel,
                Interaction.status == "open",
                contact == message.sender,
            )
            .order_by(Interaction.updated_at.desc())
            .limit(1)
        )
        idle = self._settings.conversation_idle_minutes
        if idle:
            statement = statement.where(
                Interaction.updated_at >= self._clock.now() - timedelta(minutes=idle)
            )
        found = await db.scalar(statement)
        if found is not None:
            return found
        sms = message.channel == "sms"
        interaction = new_interaction(
            message.tenant_id,
            message.profile_id,
            message.channel,
            message.sender if sms else None,
            self._clock.now(),
            customer_email=None if sms else message.sender,
        )
        db.add(interaction)
        await db.flush()
        log.info(
            "inbound_conversation_started",
            channel=message.channel,
            session_id=str(interaction.id),
            sender=mask_contact(message.sender),
        )
        return interaction
