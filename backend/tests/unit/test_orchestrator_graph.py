"""The turn graph with fake agents and a scripted model (FR-03; no database, no paid API)."""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.agents.sentiment.agent import SentimentSubtask
from app.core.config import Settings
from app.orchestrator.graph import TurnContext, TurnState, build_graph
from app.orchestrator.memory import SessionMemory
from app.orchestrator.phrasing import APOLOGY, UNCLEAR
from app.orchestrator.understanding import Task
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.providers.llm import LLMError
from tests.fakes import FakeLLM

CONFIG = load_example(Domain.RESTAURANT)


@dataclass
class FakeAgents:
    """Answers each task from `answers` (by agent name), after an optional delay."""

    answers: dict[str, AgentResult | Exception]
    delay: float = 0.0
    seen: list[Task] = field(default_factory=list)

    async def run(self, task: Task, context: AgentContext) -> AgentResult:
        self.seen.append(task)
        await asyncio.sleep(self.delay)
        answer = self.answers[task.agent]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _result(agent: str, status: AgentStatus, message: str, **extra: Any) -> AgentResult:
    return AgentResult(agent=agent, status=status, message=message, **extra)


HOURS = _result("knowledge", AgentStatus.COMPLETED, "Open 12:00 to 23:30.")
NEEDS_NAME = _result(
    "booking", AgentStatus.NEEDS_INFO, "Details needed.", missing=["customer_name"]
)


def _turn() -> TurnContext:
    tenant = uuid.uuid4()
    profile = BusinessProfile(id=uuid.uuid4(), tenant_id=tenant, config={}, version=1)
    return TurnContext(
        agent_context=AgentContext(tenant_id=tenant, profile=profile),
        config=CONFIG,
        now_local=datetime(2026, 10, 19, 10, 0, tzinfo=UTC),
        history=[],
    )


@dataclass
class FakeSentiment:
    """Returns a scripted reading (the data a SentimentAgent would report)."""

    data: dict[str, Any] | Exception
    delay: float = 0.0
    seen: list[SentimentSubtask] = field(default_factory=list)

    async def run(self, context: AgentContext, task: SentimentSubtask) -> AgentResult:
        self.seen.append(task)
        await asyncio.sleep(self.delay)
        if isinstance(self.data, Exception):
            raise self.data
        return AgentResult(
            agent="sentiment", status=AgentStatus.COMPLETED, message="", data=self.data
        )


def _reading(
    label: str = "neutral",
    *,
    reasons: list[str] | None = None,
    complaint: bool = False,
    streak: int = 0,
) -> dict[str, Any]:
    return {
        "label": label,
        "confidence": 0.9,
        "complaint": complaint,
        "escalate": bool(reasons),
        "reasons": reasons or [],
        "negative_streak": streak,
    }


async def _run(
    agents: FakeAgents,
    llm: FakeLLM | None,
    text: str = "hello",
    memory: SessionMemory | None = None,
    sentiment: FakeSentiment | None = None,
) -> TurnState:
    graph = build_graph(agents, llm, Settings(_env_file=None), sentiment)
    state = await graph.ainvoke(
        {"turn": _turn(), "text": text, "memory": memory or SessionMemory()}
    )
    return cast(TurnState, state)


TWO_INTENTS = {
    "subtasks": [
        {"id": "s1", "agent": "knowledge", "question": "When are you open?"},
        {
            "id": "s2",
            "agent": "booking",
            "booking": {"action": "create", "party_size": 4, "date": "2026-10-20", "time": "19:00"},
        },
    ],
    "confidence": 0.95,
}


async def test_tc03_independent_requests_run_in_parallel() -> None:
    agents = FakeAgents({"knowledge": HOURS, "booking": NEEDS_NAME}, delay=0.3)
    llm = FakeLLM(
        reply="We're open 12 to 11:30. May I have your name?", understandings=[TWO_INTENTS]
    )

    started = time.perf_counter()
    state = await _run(agents, llm, "When are you open? Book a table for 4 tomorrow at 7pm.")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.55  # two 0.3 s agents side by side, not one after the other
    assert {t.agent for t in agents.seen} == {"knowledge", "booking"}
    assert state["reply"] == "We're open 12 to 11:30. May I have your name?"
    assert state["outcome"] == "answered"
    assert state["memory"].draft() is not None
    assert set(state["timings"]) == {
        "understand",
        "agent.knowledge.s1",
        "agent.booking.s2",
        "reply",
    }
    # The reply step saw both results.
    assert "Open 12:00 to 23:30." in llm.calls[0][1]["content"]
    assert "customer_name" in llm.calls[0][1]["content"]


async def test_dependent_task_waits_and_is_skipped_when_its_dependency_fails() -> None:
    understanding = {
        "subtasks": [
            {"id": "s1", "agent": "knowledge", "question": "Is there valet parking?"},
            {
                "id": "s2",
                "agent": "booking",
                "booking": {"action": "create", "party_size": 2},
                "depends_on": ["s1"],
            },
        ]
    }
    no_answer = _result("knowledge", AgentStatus.NO_ANSWER, "Not covered.")
    agents = FakeAgents({"knowledge": no_answer, "booking": NEEDS_NAME})
    state = await _run(agents, FakeLLM(understandings=[understanding]))

    assert [t.agent for t in agents.seen] == ["knowledge"]
    assert state["skipped"] == ["s2"]


async def test_dependent_task_runs_after_its_dependency_completes() -> None:
    understanding = {
        "subtasks": [
            {"id": "s2", "agent": "booking", "booking": {"action": "create"}, "depends_on": ["s1"]},
            {"id": "s1", "agent": "knowledge", "question": "Are you open on Sunday?"},
        ]
    }
    agents = FakeAgents({"knowledge": HOURS, "booking": NEEDS_NAME})
    await _run(agents, FakeLLM(understandings=[understanding]))
    assert [t.agent for t in agents.seen] == ["knowledge", "booking"]


async def test_unclear_message_gets_a_question_then_a_handoff() -> None:
    unclear = {"clarification_question": "Do you want to book or ask something?", "confidence": 0.3}
    agents = FakeAgents({})

    state = await _run(agents, FakeLLM(understandings=[unclear]))
    assert state["outcome"] == "clarify"
    assert state["reply"] == "Do you want to book or ask something?"
    assert state["memory"].clarifications == 1

    low = {"confidence": 0.2}
    state = await _run(agents, FakeLLM(understandings=[low]), memory=state["memory"])
    assert state["reply"] == UNCLEAR

    limit = CONFIG.escalation.max_clarification_attempts
    memory = SessionMemory(clarifications=limit)
    state = await _run(agents, FakeLLM(understandings=[low]), memory=memory)
    assert state["outcome"] == "handoff"
    assert state["reply"] == CONFIG.escalation.handoff_message
    assert state["memory"].handed_off
    assert agents.seen == []


async def test_understood_turn_resets_the_clarification_count() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    understanding = {"subtasks": [{"id": "s1", "agent": "knowledge", "question": "Hours?"}]}
    state = await _run(
        agents, FakeLLM(understandings=[understanding]), memory=SessionMemory(clarifications=2)
    )
    assert state["memory"].clarifications == 0


async def test_invalid_understanding_is_retried_once() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    good = {"subtasks": [{"id": "s1", "agent": "knowledge", "question": "Hours?"}]}
    llm = FakeLLM(understandings=["{oops", good])
    state = await _run(agents, llm)
    assert len(llm.json_calls) == 2
    assert "not valid" in llm.json_calls[1][-1]["content"]
    assert state["outcome"] == "answered"


async def test_model_failure_falls_back_to_answering_from_knowledge() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    llm = FakeLLM(reply=LLMError("down"), understandings=[LLMError("down")])
    state = await _run(agents, llm, "When are you open?")
    assert agents.seen[0].knowledge is not None
    assert agents.seen[0].knowledge.question == "When are you open?"
    assert state["reply"] == "Open 12:00 to 23:30."  # template reply, since the model is down


async def test_without_a_model_messages_go_to_the_knowledge_agent() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    state = await _run(agents, None, "When are you open?")
    assert state["reply"] == "Open 12:00 to 23:30."


async def test_a_crashing_agent_does_not_sink_the_turn() -> None:
    agents = FakeAgents({"knowledge": HOURS, "booking": RuntimeError("db down")})
    state = await _run(agents, FakeLLM(understandings=[TWO_INTENTS]))
    booking = state["results"]["s2"]
    assert booking.status is AgentStatus.REJECTED
    assert state["results"]["s1"] == HOURS


async def test_small_talk_needs_no_agent() -> None:
    agents = FakeAgents({})
    llm = FakeLLM(reply="Hello! How can I help?", understandings=[{"subtasks": []}])
    state = await _run(agents, llm, "hi there")
    assert state["reply"] == "Hello! How can I help?"
    assert agents.seen == []


@pytest.mark.parametrize("reply", ["We're open from noon.", LLMError("timeout")])
async def test_reply_falls_back_to_a_template(reply: str | Exception) -> None:
    agents = FakeAgents({"knowledge": HOURS, "booking": NEEDS_NAME})
    state = await _run(agents, FakeLLM(reply=reply, understandings=[TWO_INTENTS]))
    if isinstance(reply, str):
        assert state["reply"] == reply
    else:
        assert state["reply"] == "Open 12:00 to 23:30. Could you tell me your name?"


# --- Sentiment side branch (FR-06, analysis C-7) ---------------------------------------


async def test_sentiment_runs_beside_understanding_on_every_message() -> None:
    agents = FakeAgents({"knowledge": HOURS, "booking": NEEDS_NAME}, delay=0.2)
    sentiment = FakeSentiment(_reading(), delay=0.2)
    llm = FakeLLM(reply="Sure.", understandings=[TWO_INTENTS])
    memory = SessionMemory(negative_streak=1)

    started = time.perf_counter()
    state = await _run(agents, llm, "When are you open?", memory=memory, sentiment=sentiment)
    assert time.perf_counter() - started < 0.55  # 0.2 s sentiment overlapped the turn

    [task] = sentiment.seen
    assert task.text == "When are you open?" and task.negative_streak == 1
    assert task.escalation == CONFIG.escalation  # the rules come from the profile
    assert state["outcome"] == "answered"
    assert state["memory"].negative_streak == 0
    assert "agent.sentiment" in state["timings"]


async def test_tc06_an_escalation_hands_over_before_any_agent_runs() -> None:
    agents = FakeAgents({"knowledge": HOURS, "booking": NEEDS_NAME})
    sentiment = FakeSentiment(_reading("negative", reasons=["negative_sentiment"], streak=1))
    state = await _run(
        agents, FakeLLM(understandings=[TWO_INTENTS]), "I'm furious", sentiment=sentiment
    )
    assert state["outcome"] == "handoff"
    assert state["reply"] == CONFIG.escalation.handoff_message
    assert state["escalation"] == ["negative_sentiment"]
    assert state["memory"].handed_off and state["memory"].negative_streak == 1
    assert agents.seen == []


async def test_an_unhappy_customer_gets_an_apology() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    sentiment = FakeSentiment(_reading("negative", complaint=True, streak=1))
    state = await _run(agents, None, "The table was dirty, when are you open?", sentiment=sentiment)
    assert state["reply"].startswith(APOLOGY)
    assert state["memory"].negative_streak == 1
    assert state.get("escalation") is None


async def test_an_unclear_hand_off_is_an_escalation_too() -> None:
    limit = CONFIG.escalation.max_clarification_attempts
    state = await _run(
        FakeAgents({}),
        FakeLLM(understandings=[{"confidence": 0.1}]),
        memory=SessionMemory(clarifications=limit),
        sentiment=FakeSentiment(_reading()),
    )
    assert state["outcome"] == "handoff" and state["escalation"] == ["unclear"]


async def test_a_failing_sentiment_model_does_not_sink_the_turn() -> None:
    agents = FakeAgents({"knowledge": HOURS})
    state = await _run(
        agents, None, "When are you open?", sentiment=FakeSentiment(RuntimeError("boom"))
    )
    assert state["outcome"] == "answered" and state["sentiment"] is None
    assert state["reply"] == "Open 12:00 to 23:30."
