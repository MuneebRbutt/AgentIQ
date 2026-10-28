"""Numbers for the dashboard's overview of one profile (FR-08-03, FR-10-02).

Everything is counted in Postgres from the records the other modules already keep
(conversations, turns, sentiment, escalations, bookings, follow-ups), always for one
tenant's profile and one period, so no separate analytics store is needed.
"""

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Float, column, func, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.models import Booking
from app.conversations.models import Interaction, SentimentRecord, Turn
from app.core.clock import Clock
from app.escalations.models import Escalation
from app.followups.models import FollowUpTask
from app.profiles.models import BusinessProfile


class Latency(BaseModel):
    """Whole-turn reply time in milliseconds (FR-10-02); None until there are replies."""

    replies: int
    average_ms: float | None
    p50_ms: float | None
    p95_ms: float | None


class AgentOutcome(BaseModel):
    agent: str
    status: str
    count: int


class Analytics(BaseModel):
    period_days: int
    since: datetime
    conversations: dict[str, int]  # by status, plus "total" and "needs_review"
    customer_messages: int
    outcomes: dict[str, int]  # how each turn ended: answered, booked, handoff, ...
    agents: list[AgentOutcome]  # what each specialist agent returned (task outcomes)
    latency: Latency
    sentiment: dict[str, int]  # negative / neutral / positive
    escalations: dict[str, int]  # by status, plus "total"
    bookings: dict[str, int]  # by status, plus "total"
    follow_ups: dict[str, int]  # by status, plus "total"


def _with_total(rows: list[Any]) -> dict[str, int]:
    counts = {str(key): int(value) for key, value in rows}
    return {"total": sum(counts.values()), **counts}


class AnalyticsService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def overview(self, profile: BusinessProfile, days: int) -> Analytics:
        since = self._clock.now() - timedelta(days=days)
        tenant, profile_id = profile.tenant_id, profile.id

        conversations = _with_total(
            list(
                await self._db.execute(
                    select(Interaction.status, func.count())
                    .where(
                        Interaction.tenant_id == tenant,
                        Interaction.profile_id == profile_id,
                        Interaction.created_at >= since,
                    )
                    .group_by(Interaction.status)
                )
            )
        )
        conversations["needs_review"] = int(
            await self._db.scalar(
                select(func.count()).where(
                    Interaction.tenant_id == tenant,
                    Interaction.profile_id == profile_id,
                    Interaction.created_at >= since,
                    Interaction.needs_review.is_(True),
                )
            )
            or 0
        )

        # Turns of this profile's conversations in the period.
        turns = (
            select(Turn.role, Turn.data)
            .join(Interaction, Interaction.id == Turn.interaction_id)
            .where(
                Turn.tenant_id == tenant,
                Interaction.profile_id == profile_id,
                Turn.created_at >= since,
            )
            .subquery()
        )
        customer_messages = int(
            await self._db.scalar(
                select(func.count()).select_from(turns).where(turns.c.role == "customer")
            )
            or 0
        )
        replies = select(turns.c.data).where(turns.c.role == "assistant").subquery()
        outcome = replies.c.data["outcome"].astext
        outcomes = {
            str(key): int(value)
            for key, value in await self._db.execute(
                select(outcome, func.count()).where(outcome.is_not(None)).group_by(outcome)
            )
        }

        # One row per agent result in each reply (every reply stores a list, maybe empty).
        result = (
            func.jsonb_array_elements(replies.c.data["results"])
            .table_valued(column("value", JSONB))
            .lateral()
        )
        agent = result.c.value["agent"].astext
        agent_status = result.c.value["status"].astext
        agents = [
            AgentOutcome(agent=str(name), status=str(state), count=int(count))
            for name, state, count in await self._db.execute(
                select(agent, agent_status, func.count())
                .select_from(replies)
                .join(result, true())
                .group_by(agent, agent_status)
                .order_by(agent, agent_status)
            )
        ]

        total_ms = replies.c.data["timings"]["total"].astext.cast(Float)
        latency_row = (
            await self._db.execute(
                select(
                    func.count(total_ms),
                    func.avg(total_ms),
                    func.percentile_cont(0.5).within_group(total_ms),
                    func.percentile_cont(0.95).within_group(total_ms),
                )
            )
        ).one()
        latency = Latency(
            replies=int(latency_row[0]),
            average_ms=_round(latency_row[1]),
            p50_ms=_round(latency_row[2]),
            p95_ms=_round(latency_row[3]),
        )

        sentiment = {
            str(key): int(value)
            for key, value in await self._db.execute(
                select(SentimentRecord.label, func.count())
                .join(Interaction, Interaction.id == SentimentRecord.interaction_id)
                .where(
                    SentimentRecord.tenant_id == tenant,
                    Interaction.profile_id == profile_id,
                    SentimentRecord.created_at >= since,
                )
                .group_by(SentimentRecord.label)
            )
        }
        escalations = _with_total(
            list(
                await self._db.execute(
                    select(Escalation.status, func.count())
                    .where(
                        Escalation.tenant_id == tenant,
                        Escalation.profile_id == profile_id,
                        Escalation.created_at >= since,
                    )
                    .group_by(Escalation.status)
                )
            )
        )
        bookings = _with_total(
            list(
                await self._db.execute(
                    select(Booking.status, func.count())
                    .where(
                        Booking.tenant_id == tenant,
                        Booking.profile_id == profile_id,
                        Booking.created_at >= since,
                    )
                    .group_by(Booking.status)
                )
            )
        )
        follow_ups = _with_total(
            list(
                await self._db.execute(
                    select(FollowUpTask.status, func.count())
                    .where(
                        FollowUpTask.tenant_id == tenant,
                        FollowUpTask.profile_id == profile_id,
                        FollowUpTask.created_at >= since,
                    )
                    .group_by(FollowUpTask.status)
                )
            )
        )
        return Analytics(
            period_days=days,
            since=since,
            conversations=conversations,
            customer_messages=customer_messages,
            outcomes=outcomes,
            agents=agents,
            latency=latency,
            sentiment=sentiment,
            escalations=escalations,
            bookings=bookings,
            follow_ups=follow_ups,
        )


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 1)
