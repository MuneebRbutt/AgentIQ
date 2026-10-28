"""The Follow-Up worker: turns events into messages and sends them when due (FR-07; ADR-0007).

Each pass (`run_once`):

1. closes conversations nobody has written in for `CONVERSATION_IDLE_MINUTES` (M-14);
2. hands every new booking event (created / modified / cancelled) to the Follow-Up agent
   and queues the messages it plans: a confirmation now, reminders before the booking;
3. does the same for every conversation that has ended: a feedback request, if enabled;
4. sends every queued message that is due; a failure is logged and tried once more after
   `FOLLOWUP_RETRY_MINUTES` (UC-07), then marked failed.

Postgres is the queue (NFR04-02). Rows are taken with `FOR UPDATE SKIP LOCKED`, one per
transaction, so any number of workers can run side by side without double-handling, and a
crash just leaves the row for the next pass. Delivery is at-least-once: a crash between a
provider accepting a message and the commit can resend it.

The worker serves every business, so it isn't tied to a login; each row it touches carries
its tenant, and everything it loads for that row is scoped to it (NFR03-02).
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast, get_args

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.followup.agent import (
    BookingFacts,
    ConversationFacts,
    EventKind,
    FollowUpAgent,
    FollowUpPlan,
)
from app.bookings.models import Booking, BookingEvent, Customer
from app.bookings.service import phone_key, profile_config
from app.conversations.models import Interaction, Turn
from app.core.clock import Clock
from app.core.config import Settings
from app.core.logging import get_logger
from app.escalations.models import Escalation
from app.escalations.service import notice_for
from app.followups.models import FollowUpTask
from app.followups.service import MAX_ATTEMPTS, cancel_pending_for_booking, new_task
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig
from app.providers.notifications import Notice, Notifier
from app.tenancy.scoping import scoped

log = get_logger(__name__)

SEND_TIMEOUT_SECONDS = 15.0
BATCH_SIZE = 100  # per step and pass, so one pass never runs unbounded
EVENT_KINDS = frozenset(get_args(EventKind))


@dataclass
class PassReport:
    closed_idle: int = 0
    booking_events: int = 0
    conversations: int = 0
    queued: int = 0
    delivered: dict[str, int] = field(default_factory=dict)  # final status -> count

    def count(self, status: str) -> None:
        self.delivered[status] = self.delivered.get(status, 0) + 1


class FollowUpWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        notifier: Notifier,
        agent: FollowUpAgent | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._notifier = notifier
        self._agent = agent or FollowUpAgent()

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("followup_worker_started", poll_seconds=self._settings.followup_poll_seconds)
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # never let one bad pass stop the worker (NFR04-01)
                log.exception("followup_pass_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self._settings.followup_poll_seconds)
        log.info("followup_worker_stopped")

    async def run_once(self) -> PassReport:
        report = PassReport()
        report.closed_idle = await self._close_idle_conversations()
        for _ in range(BATCH_SIZE):
            if not await self._next_booking_event(report):
                break
            report.booking_events += 1
        for _ in range(BATCH_SIZE):
            if not await self._next_ended_conversation(report):
                break
            report.conversations += 1
        for _ in range(BATCH_SIZE):
            if not await self._next_due_message(report):
                break
        if report.booking_events or report.conversations or report.delivered:
            log.info(
                "followup_pass",
                closed_idle=report.closed_idle,
                booking_events=report.booking_events,
                conversations=report.conversations,
                queued=report.queued,
                delivered=report.delivered,
            )
        return report

    # -- 1. conversations that went quiet -------------------------------------------------

    async def _close_idle_conversations(self) -> int:
        minutes = self._settings.conversation_idle_minutes
        if minutes == 0:
            return 0
        cutoff = self._clock.now() - timedelta(minutes=minutes)
        async with self._session_factory() as db:
            # updated_at stays the time of the last message: that is when it really ended.
            result = await db.execute(
                update(Interaction)
                .where(Interaction.status == "open", Interaction.updated_at <= cutoff)
                .values(status="closed")
            )
            await db.commit()
        return int(getattr(result, "rowcount", 0) or 0)

    # -- 2. booking events ----------------------------------------------------------------

    async def _next_booking_event(self, report: PassReport) -> bool:
        async with self._session_factory() as db:
            event = await db.scalar(
                select(BookingEvent)
                .where(BookingEvent.followed_up_at.is_(None))
                .order_by(BookingEvent.seq)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                return False
            now = self._clock.now()
            booking = await db.get(Booking, event.booking_id)
            if booking is not None:
                await self._follow_up_booking(db, event, booking, now, report)
            event.followed_up_at = now
            await db.commit()
        return True

    async def _follow_up_booking(
        self,
        db: AsyncSession,
        event: BookingEvent,
        booking: Booking,
        now: datetime,
        report: PassReport,
    ) -> None:
        config = await self._config(db, booking.tenant_id, booking.profile_id)
        customer = await db.scalar(
            scoped(select(Customer), Customer, booking.tenant_id).where(
                Customer.id == booking.customer_id
            )
        )
        if config is None or customer is None:
            return
        facts = BookingFacts(
            reference=booking.reference,
            status=booking.status,
            service_id=booking.service_id,
            party_size=booking.party_size,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            customer_name=customer.name,
            customer_phone=customer.phone,
            customer_email=customer.email,
        )
        if event.kind not in EVENT_KINDS:
            return
        plan = self._agent.for_booking(cast(EventKind, event.kind), facts, config, now)
        cancelled = 0
        if plan.cancel_pending:
            cancelled = await cancel_pending_for_booking(db, booking.tenant_id, booking.id, now)
        for message in plan.messages:
            db.add(
                new_task(booking.tenant_id, booking.profile_id, message, now, booking_id=booking.id)
            )
        report.queued += len(plan.messages)
        self._log_plan(
            plan, booking.tenant_id, booking_id=str(booking.id), booking_event=event.kind
        )
        if cancelled:
            log.info("followups_cancelled", booking_id=str(booking.id), count=cancelled)

    # -- 3. ended conversations -----------------------------------------------------------

    async def _next_ended_conversation(self, report: PassReport) -> bool:
        async with self._session_factory() as db:
            interaction = await db.scalar(
                select(Interaction)
                .where(Interaction.status != "open", Interaction.followed_up_at.is_(None))
                .order_by(Interaction.updated_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if interaction is None:
                return False
            now = self._clock.now()
            config = await self._config(db, interaction.tenant_id, interaction.profile_id)
            if config is not None:
                facts = await self._conversation_facts(db, interaction)
                plan = self._agent.for_conversation(facts, config)
                for message in plan.messages:
                    db.add(
                        new_task(
                            interaction.tenant_id,
                            interaction.profile_id,
                            message,
                            now,
                            interaction_id=interaction.id,
                        )
                    )
                report.queued += len(plan.messages)
                self._log_plan(plan, interaction.tenant_id, session_id=str(interaction.id))
            interaction.followed_up_at = now
            await db.commit()
        return True

    async def _conversation_facts(
        self, db: AsyncSession, interaction: Interaction
    ) -> ConversationFacts:
        said = await db.scalar(
            scoped(select(func.count()).select_from(Turn), Turn, interaction.tenant_id).where(
                Turn.interaction_id == interaction.id, Turn.role == "customer"
            )
        )
        customer = None
        if interaction.customer_phone:
            # The caller may have booked before: greet them by name, and allow email.
            customer = await db.scalar(
                scoped(select(Customer), Customer, interaction.tenant_id).where(
                    Customer.phone_key == phone_key(interaction.customer_phone)
                )
            )
        return ConversationFacts(
            status=interaction.status,
            ended_at=interaction.updated_at,
            customer_messages=int(said or 0),
            customer_name=customer.name if customer else None,
            customer_phone=interaction.customer_phone,
            # Someone who wrote by email is answered there (ADR-0011).
            customer_email=interaction.customer_email or (customer.email if customer else None),
        )

    # -- 4. sending -----------------------------------------------------------------------

    async def _next_due_message(self, report: PassReport) -> bool:
        async with self._session_factory() as db:
            now = self._clock.now()
            task = await db.scalar(
                select(FollowUpTask)
                .where(FollowUpTask.status == "pending", FollowUpTask.due_at <= now)
                .order_by(FollowUpTask.due_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if task is None:
                return False
            await self._deliver(db, task, now)
            task.updated_at = now
            report.count(task.status)
            await db.commit()
        return True

    async def _deliver(self, db: AsyncSession, task: FollowUpTask, now: datetime) -> None:
        notice = await self._notice(db, task, now)
        if notice is None:
            task.status = "cancelled"
            return
        task.attempts += 1
        try:
            delivery = await asyncio.wait_for(self._notifier.send(notice), SEND_TIMEOUT_SECONDS)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            task.last_error = error[:300]
            if task.attempts >= MAX_ATTEMPTS:
                task.status = "failed"
                log.error(
                    "followup_failed",
                    tenant_id=str(task.tenant_id),
                    follow_up_id=str(task.id),
                    kind=task.kind,
                    error=task.last_error,
                )
            else:
                task.due_at = now + timedelta(minutes=self._settings.followup_retry_minutes)
                log.warning(
                    "followup_retry_scheduled",
                    tenant_id=str(task.tenant_id),
                    follow_up_id=str(task.id),
                    kind=task.kind,
                    error=task.last_error,
                )
            return
        task.status = delivery
        task.sent_at = now
        if task.escalation_id is not None:
            escalation = await db.get(Escalation, task.escalation_id)
            if escalation is not None:
                escalation.notification = delivery
                escalation.notified_at = now
                escalation.updated_at = now

    async def _notice(self, db: AsyncSession, task: FollowUpTask, now: datetime) -> Notice | None:
        """What to send, or None when the message no longer makes sense."""
        if task.kind == "escalation":
            escalation = await db.get(Escalation, task.escalation_id)
            config = await self._config(db, task.tenant_id, task.profile_id)
            if escalation is None or config is None:
                return None
            # The business's contacts as they are now, in case the operator fixed them.
            return notice_for(escalation, config)
        if task.booking_id is not None:
            booking = await db.scalar(
                scoped(select(Booking), Booking, task.tenant_id).where(
                    Booking.id == task.booking_id
                )
            )
            # Belt and braces: the cancel event normally withdraws these first.
            if booking is None or booking.status != "confirmed":
                return None
            if task.kind == "reminder" and booking.starts_at <= now:
                return None
        sms = task.channel == "sms"
        return Notice(
            subject=task.subject,
            body=task.body,
            phone=task.recipient if sms else None,
            email=None if sms else task.recipient,
        )

    # -- helpers --------------------------------------------------------------------------

    async def _config(
        self, db: AsyncSession, tenant_id: uuid.UUID, profile_id: uuid.UUID
    ) -> BusinessProfileConfig | None:
        profile = await db.scalar(
            scoped(select(BusinessProfile), BusinessProfile, tenant_id).where(
                BusinessProfile.id == profile_id
            )
        )
        if profile is None:
            return None
        try:
            return profile_config(profile)
        except ValidationError:
            log.warning("followup_profile_invalid", profile_id=str(profile_id))
            return None

    @staticmethod
    def _log_plan(plan: FollowUpPlan, tenant_id: uuid.UUID, **where: str) -> None:
        # FR-07-01: the decision itself is recorded, including "nothing to do".
        log.info(
            "followup_decided",
            tenant_id=str(tenant_id),
            planned=[message.kind for message in plan.messages],
            skipped=plan.skipped,
            **where,
        )
