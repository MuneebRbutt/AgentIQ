"""The orchestrator's entry point: one customer message in, one reply out (FR-03, FR-09-05).

Every channel (the conversation API and test console, voice, SMS and email) and the evaluation
harness call `Orchestrator.handle` with a `NormalizedRequest`. The tenant and profile come
from the channel (login or phone number), never from the conversation (NFR03-02).
"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.booking.agent import BookingAgent
from app.agents.contracts import AgentContext, AgentResult
from app.agents.knowledge.agent import KnowledgeAgent
from app.bookings.service import BookingService
from app.conversations.models import Interaction, SentimentRecord, Turn
from app.conversations.service import CONVERSATION_NOT_FOUND, new_interaction
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.escalations.service import EscalationService, new_escalation
from app.knowledge.embeddings import Embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.service import KnowledgeService
from app.knowledge.store import ChromaVectorStore
from app.orchestrator.graph import (
    AgentRunner,
    Outcome,
    SentimentRunner,
    TurnContext,
    TurnState,
    build_graph,
)
from app.orchestrator.memory import SessionMemory
from app.orchestrator.understanding import Task
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig
from app.profiles.service import PROFILE_NOT_FOUND
from app.providers.llm import LLMClient
from app.providers.notifications import LogNotifier, Notifier
from app.tenancy.scoping import scoped

log = get_logger(__name__)

CONVERSATION_ENDED = {
    "handed_off": "This conversation was handed over to a person.",
    "closed": "This conversation has ended. Start a new one.",
}


class NormalizedRequest(BaseModel):
    """A customer's message, whatever channel it came from (CLAUDE.md §3)."""

    tenant_id: uuid.UUID
    profile_id: uuid.UUID
    session_id: uuid.UUID | None = None  # None starts a new conversation
    channel: Literal["text", "sms", "voice", "email"] = "text"
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    # Caller ID or SMS sender, when the channel knows it (analysis AS-5).
    customer_phone: Annotated[str, StringConstraints(max_length=32)] | None = None


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    session_id: uuid.UUID
    reply: str
    outcome: Outcome
    results: list[AgentResult] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # per stage, ms (FR-10-02)
    handed_off: bool = False
    sentiment: AgentResult | None = None  # how the customer's message sounded (FR-06-01)
    escalation_id: uuid.UUID | None = None  # set when this turn escalated (FR-06-04)


class Agents:
    """Runs a task on its specialist. Each task gets its own database session, so agents
    running in parallel never share one."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        store: ChromaVectorStore,
        embedder: Embedder,
        facts: ProfileFactsIndex | None,
        llm: LLMClient | None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._store = store
        self._embedder = embedder
        self._facts = facts
        self._llm = llm

    async def run(self, task: Task, context: AgentContext) -> AgentResult:
        async with self._session_factory() as db:
            if task.booking is not None:
                return await BookingAgent(BookingService(db, self._clock)).run(
                    context, task.booking
                )
            if task.knowledge is not None:
                knowledge = KnowledgeService(
                    db, self._clock, self._settings, self._store, self._embedder, self._facts
                )
                return await KnowledgeAgent(knowledge, self._llm).run(context, task.knowledge)
        raise ValueError(f"task {task.id} has no subtask")


class Orchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        settings: Settings,
        agents: AgentRunner,
        llm: LLMClient | None,
        sentiment: SentimentRunner | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._settings = settings
        self._notifier = notifier or LogNotifier()
        self._graph = build_graph(agents, llm, settings, sentiment)

    async def handle(self, request: NormalizedRequest) -> TurnOutcome:
        start = time.perf_counter()
        async with self._session_factory() as db:
            profile = await self._profile(db, request)
            interaction = await self._interaction(db, request)
            config = BusinessProfileConfig.model_validate(profile.config)
            now = self._clock.now()
            turn = TurnContext(
                agent_context=AgentContext(
                    tenant_id=request.tenant_id,
                    profile=profile,
                    customer_phone=interaction.customer_phone,
                ),
                config=config,
                now_local=now.astimezone(ZoneInfo(config.timezone)),
                history=await self._history(db, interaction),
            )
            state = cast(
                TurnState,
                await self._graph.ainvoke(
                    {
                        "turn": turn,
                        "text": request.text,
                        "memory": SessionMemory.model_validate(interaction.memory),
                    }
                ),
            )
            timings = {**state.get("timings", {}), "total": _ms(start)}
            memory = state["memory"]
            results = [
                result
                for task in state.get("tasks", [])
                if (result := state.get("results", {}).get(task.id)) is not None
            ]
            customer_seq = interaction.turn_count + 1
            sentiment = state.get("sentiment")
            self._record(db, interaction, request.text, state, results, timings)
            if sentiment is not None:
                db.add(_sentiment_record(interaction, customer_seq, sentiment, now))
                if sentiment.data.get("low_confidence"):
                    interaction.needs_review = True  # UC-06: a person should look
            escalation = None
            if reasons := state.get("escalation"):
                escalation = new_escalation(
                    interaction, customer_seq, reasons, request.text, sentiment, config, now
                )
                db.add(escalation)
            interaction.memory = memory.model_dump(mode="json")
            interaction.status = "handed_off" if memory.handed_off else interaction.status
            interaction.updated_at = now
            session_id = interaction.id
            await db.commit()
            if escalation is not None:
                log.info("escalated", session_id=str(session_id), reasons=reasons)
                await EscalationService(db, self._clock).notify(
                    escalation.id,
                    config,
                    self._notifier,
                    retry_after=timedelta(minutes=self._settings.followup_retry_minutes),
                )

        log.info(
            "turn_handled",
            session_id=str(session_id),
            profile_id=str(request.profile_id),
            channel=request.channel,
            outcome=state["outcome"],
            agents=[r.agent for r in results],
            total_ms=timings["total"],
        )
        return TurnOutcome(
            session_id=session_id,
            reply=state["reply"],
            outcome=state["outcome"],
            results=results,
            timings=timings,
            handed_off=memory.handed_off,
            sentiment=sentiment,
            escalation_id=escalation.id if escalation is not None else None,
        )

    async def _profile(self, db: AsyncSession, request: NormalizedRequest) -> BusinessProfile:
        statement = scoped(select(BusinessProfile), BusinessProfile, request.tenant_id).where(
            BusinessProfile.id == request.profile_id
        )
        profile = await db.scalar(statement)
        if profile is None:
            raise NotFoundError(PROFILE_NOT_FOUND)
        return profile

    async def _interaction(self, db: AsyncSession, request: NormalizedRequest) -> Interaction:
        if request.session_id is None:
            interaction = new_interaction(
                request.tenant_id,
                request.profile_id,
                request.channel,
                request.customer_phone,
                self._clock.now(),
            )
            db.add(interaction)
            await db.flush()
            return interaction
        # Locked for the turn, so two messages in one conversation are handled in order.
        statement = (
            scoped(select(Interaction), Interaction, request.tenant_id)
            .where(
                Interaction.id == request.session_id,
                Interaction.profile_id == request.profile_id,
            )
            .with_for_update()
        )
        found = await db.scalar(statement)
        if found is None:
            raise NotFoundError(CONVERSATION_NOT_FOUND)
        if found.status != "open":
            # A person has taken over, or the conversation ended (FR-06-03).
            raise ConflictError(CONVERSATION_ENDED[found.status])
        return found

    async def _history(self, db: AsyncSession, interaction: Interaction) -> list[dict[str, str]]:
        limit = self._settings.conversation_history_turns
        if limit == 0 or interaction.turn_count == 0:
            return []
        statement = (
            select(Turn.role, Turn.text)
            .where(Turn.interaction_id == interaction.id)
            .order_by(Turn.seq.desc())
            .limit(limit)
        )
        rows = (await db.execute(statement)).all()
        return [{"role": role, "text": text} for role, text in reversed(rows)]

    def _record(
        self,
        db: AsyncSession,
        interaction: Interaction,
        text: str,
        state: TurnState,
        results: list[AgentResult],
        timings: dict[str, float],
    ) -> None:
        seq = interaction.turn_count
        now = self._clock.now()
        db.add(
            Turn(
                tenant_id=interaction.tenant_id,
                interaction_id=interaction.id,
                seq=seq + 1,
                role="customer",
                text=text,
                data={},
                created_at=now,
            )
        )
        db.add(
            Turn(
                tenant_id=interaction.tenant_id,
                interaction_id=interaction.id,
                seq=seq + 2,
                role="assistant",
                text=state["reply"],
                data={
                    "outcome": state["outcome"],
                    "understanding": state.get("understanding"),
                    "results": [r.model_dump(mode="json") for r in results],
                    "skipped": state.get("skipped", []),
                    "sentiment": _dump(state.get("sentiment")),
                    "escalation": state.get("escalation"),
                    "timings": timings,
                },
                created_at=now,
            )
        )
        interaction.turn_count = seq + 2


def _dump(result: AgentResult | None) -> dict[str, Any] | None:
    return result.model_dump(mode="json") if result is not None else None


def _sentiment_record(
    interaction: Interaction, turn_seq: int, result: AgentResult, now: datetime
) -> SentimentRecord:
    data = result.data
    return SentimentRecord(
        tenant_id=interaction.tenant_id,
        interaction_id=interaction.id,
        turn_seq=turn_seq,
        label=data["label"],
        confidence=data["confidence"],
        scores=data["scores"],
        model=data["model"],
        low_confidence=data["low_confidence"],
        complaint=data["complaint"],
        reasons=data["reasons"],
        created_at=now,
    )


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
