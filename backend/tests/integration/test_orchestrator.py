"""Whole conversations through the orchestrator against the real database (FR-03, FR-09-05).

The model is scripted (FakeLLM): each turn's understanding is what a model would return,
so these tests check everything after it (routing, agents, memory, persistence) for real.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.bookings.models import Booking
from app.conversations.models import Interaction, Turn
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.orchestrator.service import Agents, NormalizedRequest, Orchestrator, TurnOutcome
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.providers.llm import LLMClient
from app.tenancy.models import Tenant
from tests.fakes import FakeLLM
from tests.integration.conftest import FakeClock, OperatorFactory, next_tuesday

pytestmark = pytest.mark.integration

MakeProfile = Callable[[Tenant, Domain], Awaitable[BusinessProfile]]
PHONE = "+92 300 1234567"


def _orchestrator(
    app: FastAPI, settings: Settings, clock: FakeClock, llm: LLMClient | None
) -> Orchestrator:
    """Built from the app's shared pieces, the way a channel will build it (M6)."""
    state = app.state
    agents = Agents(
        state.session_factory,
        clock,
        settings,
        state.vector_store,
        state.embedder,
        state.profile_facts,
        llm,
    )
    return Orchestrator(state.session_factory, clock, settings, agents, llm)


class Conversation:
    def __init__(self, orchestrator: Orchestrator, profile: BusinessProfile) -> None:
        self.orchestrator = orchestrator
        self.tenant_id = profile.tenant_id
        self.profile_id = profile.id
        self.session_id: uuid.UUID | None = None

    async def say(self, text: str) -> TurnOutcome:
        outcome = await self.orchestrator.handle(
            NormalizedRequest(
                tenant_id=self.tenant_id,
                profile_id=self.profile_id,
                session_id=self.session_id,
                text=text,
                customer_phone=PHONE,
            )
        )
        self.session_id = outcome.session_id
        return outcome


@pytest.fixture
async def restaurant(factory: OperatorFactory, make_profile: MakeProfile) -> BusinessProfile:
    return await make_profile(await factory.tenant("Spice Garden"), Domain.RESTAURANT)


def _book(**fields: Any) -> dict[str, Any]:
    return {"id": "b1", "agent": "booking", "booking": {"action": "create", **fields}}


async def test_tc03_hours_and_a_table_for_four_over_three_turns(
    live_app: FastAPI,
    integration_settings: Settings,
    clock: FakeClock,
    restaurant: BusinessProfile,
    db: AsyncSession,
) -> None:
    day = next_tuesday(clock)
    llm = FakeLLM(
        understandings=[
            {
                "subtasks": [
                    {"id": "k1", "agent": "knowledge", "question": "What are your opening hours?"},
                    _book(service_id="dine-in", party_size=4, date=str(day), time="19:30"),
                ]
            },
            {"subtasks": [_book(customer_name="Ali Raza")]},
            {"confirmation": "yes"},
        ]
    )
    chat = Conversation(_orchestrator(live_app, integration_settings, clock, llm), restaurant)

    # Turn 1: two intents, handled by two agents in the same turn (TC-03).
    first = await chat.say(
        "What are your opening hours? And book a table for 4 on Tuesday at 7:30pm."
    )
    statuses = {r.agent: r.status.value for r in first.results}
    assert statuses == {"knowledge": "completed", "booking": "needs_info"}
    hours = next(r for r in first.results if r.agent == "knowledge")
    best = hours.data["sources"][0]
    assert (best["filename"], best["chunk_index"]) == ("business profile", 0)  # opening hours
    # The knowledge passages went to the model that wrote the answer.
    assert "Opening hours: Monday to Thursday 12:00 to 15:30" in llm.calls[0][1]["content"]
    booking_ask = next(r for r in first.results if r.agent == "booking")
    assert booking_ask.missing == ["customer_name"]  # the phone came from caller ID

    # Turn 2: the name completes the booking; it is read back, not booked (FR-04-03).
    second = await chat.say("Ali Raza")
    [read_back] = second.results
    assert read_back.status.value == "needs_confirmation"
    assert "Table for 4" in read_back.message
    assert await db.scalar(select(func.count()).select_from(Booking)) == 0

    # Turn 3: "yes" commits exactly what was read back.
    third = await chat.say("Yes please")
    [booked] = third.results
    assert booked.status.value == "completed"
    booking = await db.scalar(select(Booking))
    assert booking is not None
    assert booking.party_size == 4 and booking.resource_id == "table-4"
    assert booking.reference in booked.message

    # The conversation is stored turn by turn with timings (FR-09-05, FR-10-02).
    turns = list(await db.scalars(select(Turn).order_by(Turn.seq)))
    assert [t.role for t in turns] == ["customer", "assistant"] * 3
    assert "understand" in turns[1].data["timings"] and "total" in turns[1].data["timings"]
    interaction = await db.get(Interaction, chat.session_id)
    assert interaction is not None and interaction.memory["booking"] is None


async def test_tc09_conversation_survives_a_restart(
    live_app: FastAPI,
    integration_settings: Settings,
    clock: FakeClock,
    restaurant: BusinessProfile,
    db: AsyncSession,
) -> None:
    """The booking in progress lives in Postgres, not in the process (NFR04-03)."""
    day = next_tuesday(clock)
    before = FakeLLM(
        understandings=[
            {"subtasks": [_book(service_id="dine-in", party_size=2, date=str(day), time="20:00")]}
        ]
    )
    chat = Conversation(_orchestrator(live_app, integration_settings, clock, before), restaurant)
    await chat.say("A table for two on Tuesday at 8pm")

    # A brand-new orchestrator (as after a backend restart) picks up the same conversation.
    after = FakeLLM(
        understandings=[{"subtasks": [_book(customer_name="Sara Khan")]}, {"confirmation": "yes"}]
    )
    resumed = Conversation(_orchestrator(live_app, integration_settings, clock, after), restaurant)
    resumed.session_id = chat.session_id
    read_back = await resumed.say("It's Sara Khan")
    assert read_back.results[0].status.value == "needs_confirmation"
    assert "Table for 2" in read_back.results[0].message
    # The model was shown the earlier turns and the booking in progress.
    context = after.json_calls[0][1]["content"]
    assert "A table for two" in context and '"party_size": 2' in context

    done = await resumed.say("yes")
    assert done.results[0].status.value == "completed"
    assert await db.scalar(select(func.count()).select_from(Booking)) == 1


async def test_a_yes_without_a_read_back_books_nothing(
    live_app: FastAPI,
    integration_settings: Settings,
    clock: FakeClock,
    restaurant: BusinessProfile,
    db: AsyncSession,
) -> None:
    day = next_tuesday(clock)
    llm = FakeLLM(
        understandings=[
            {
                "subtasks": [
                    _book(
                        service_id="dine-in",
                        party_size=2,
                        date=str(day),
                        time="20:00",
                        customer_name="A",
                        confirmed=True,
                    )
                ],
                "confirmation": "yes",
            }
        ]
    )
    chat = Conversation(_orchestrator(live_app, integration_settings, clock, llm), restaurant)
    outcome = await chat.say("Book a table for 2 on Tuesday at 8, name A, and yes I confirm")
    assert outcome.results[0].status.value == "needs_confirmation"
    assert await db.scalar(select(func.count()).select_from(Booking)) == 0


async def test_without_a_model_questions_are_answered_from_the_profile(
    live_app: FastAPI, integration_settings: Settings, clock: FakeClock, restaurant: BusinessProfile
) -> None:
    chat = Conversation(_orchestrator(live_app, integration_settings, clock, None), restaurant)
    outcome = await chat.say("Do you serve halal food?")
    assert outcome.reply == "Do you serve halal food? Yes, all our food is halal."
    assert outcome.outcome == "answered"


async def test_handoff_marks_the_conversation(
    live_app: FastAPI,
    integration_settings: Settings,
    clock: FakeClock,
    restaurant: BusinessProfile,
    db: AsyncSession,
) -> None:
    unclear = {"confidence": 0.1}
    llm = FakeLLM(understandings=[unclear] * 4)
    chat = Conversation(_orchestrator(live_app, integration_settings, clock, llm), restaurant)
    outcomes = [await chat.say("mmm") for _ in range(4)]
    assert [o.outcome for o in outcomes] == ["clarify", "clarify", "clarify", "handoff"]
    assert outcomes[-1].handed_off
    interaction = await db.get(Interaction, chat.session_id)
    assert interaction is not None and interaction.status == "handed_off"


async def test_tenants_and_conversations_are_isolated(
    live_app: FastAPI,
    integration_settings: Settings,
    clock: FakeClock,
    restaurant: BusinessProfile,
    factory: OperatorFactory,
    make_profile: MakeProfile,
) -> None:
    orchestrator = _orchestrator(live_app, integration_settings, clock, None)
    chat = Conversation(orchestrator, restaurant)
    await chat.say("Is there a kids' menu?")

    rival = await make_profile(await factory.tenant("Rival"), Domain.RESTAURANT)
    # Another business can't continue our conversation...
    with pytest.raises(NotFoundError):
        await orchestrator.handle(
            NormalizedRequest(
                tenant_id=rival.tenant_id,
                profile_id=rival.id,
                session_id=chat.session_id,
                text="hello",
            )
        )
    # ...or talk as our profile.
    with pytest.raises(NotFoundError):
        await orchestrator.handle(
            NormalizedRequest(tenant_id=rival.tenant_id, profile_id=restaurant.id, text="hi")
        )
    # Nor can a conversation move to another profile of ours.
    other = await make_profile(await factory.tenant("Spice Garden 2"), Domain.CLINIC)
    with pytest.raises(NotFoundError):
        await orchestrator.handle(
            NormalizedRequest(
                tenant_id=restaurant.tenant_id,
                profile_id=other.id,
                session_id=chat.session_id,
                text="hi",
            )
        )


async def test_chat_command(
    live_app: FastAPI,
    integration_settings: Settings,
    restaurant: BusinessProfile,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = iter(["Is there a kids' menu?", "quit"])
    await cli.chat(integration_settings, restaurant.id, None, read=lambda prompt: next(lines))
    out = capsys.readouterr().out
    assert "Assistant: Assalam-o-Alaikum, welcome to Spice Garden!" in out
    assert "kids' menu and high chairs" in out


async def test_chat_command_needs_a_profile(integration_settings: Settings) -> None:
    with pytest.raises(SystemExit, match="No profile"):
        await cli.chat(integration_settings, uuid.uuid4(), None, read=lambda prompt: "quit")


async def test_chat_command_stops_when_handed_over(
    live_app: FastAPI,
    integration_settings: Settings,
    restaurant: BusinessProfile,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = iter(["This is the third time my booking has been wrong, I'm furious.", "hello?"])
    await cli.chat(integration_settings, restaurant.id, None, read=lambda prompt: next(lines))
    out = capsys.readouterr().out
    assert "negative 0.94]" in out
    assert "Handed over to a person" in out
