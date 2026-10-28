"""The orchestration graph that runs one conversation turn (FR-03-01…05; ADR-0005).

    start ──▶ understand ─┐
      └────▶ sentiment ──┴─▶ decide ──escalate──▶ escalate ──▶ end
                               │ ──unclear───▶ clarify ──▶ end
                               ▼
    dispatch ──ready tasks (in parallel)──▶ run_agent ──▶ dispatch ...
        │
        └──nothing left──▶ reply ──▶ end

Sentiment runs on every customer message, at the same time as understanding (FR-06-01,
analysis C-7); `decide` applies its verdict before anything else happens.

The graph holds no conversation state of its own: the orchestrator service loads the
session's memory before the turn and saves it after, so nothing is lost on a restart.
"""

import operator
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.agents.knowledge.agent import KnowledgeSubtask
from app.agents.sentiment.agent import SentimentSubtask
from app.core.config import Settings
from app.core.logging import get_logger
from app.orchestrator.memory import SessionMemory, remember_result
from app.orchestrator.phrasing import NOTE_UPSET, UNCLEAR, template_reply
from app.orchestrator.planning import blocked_tasks, plan, ready_tasks
from app.orchestrator.prompting import reply_messages, understand_messages
from app.orchestrator.understanding import (
    Parsed,
    Task,
    Understanding,
    UnderstandingError,
    parse_understanding,
)
from app.profiles.schema import BusinessProfileConfig
from app.providers.llm import ChatMessage, LLMClient, LLMError

log = get_logger(__name__)

Outcome = Literal["answered", "clarify", "handoff"]

NOTE_UNDERSTANDING_FAILED = "understanding_failed"
REASON_UNCLEAR = "unclear"
UNDERSTAND_ATTEMPTS = 2


class AgentRunner(Protocol):
    async def run(self, task: Task, context: AgentContext) -> AgentResult: ...


class SentimentRunner(Protocol):
    async def run(self, context: AgentContext, task: SentimentSubtask) -> AgentResult: ...


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Facts fixed for the whole turn. They come from the channel and the database."""

    agent_context: AgentContext
    config: BusinessProfileConfig
    now_local: datetime
    history: list[dict[str, str]]


def _merge[V](left: dict[str, V], right: dict[str, V]) -> dict[str, V]:
    return {**left, **right}


class TurnState(TypedDict, total=False):
    turn: TurnContext
    text: str
    memory: SessionMemory
    understanding: dict[str, Any] | None
    tasks: list[Task]
    results: Annotated[dict[str, AgentResult], _merge]
    skipped: Annotated[list[str], operator.add]
    notes: list[str]
    outcome: Outcome
    sentiment: AgentResult | None
    escalation: list[str] | None  # reasons, when the conversation goes to a person
    reply: str
    timings: Annotated[dict[str, float], _merge]
    task: Task  # only in the input of one run_agent call


def build_graph(
    agents: AgentRunner,
    llm: LLMClient | None,
    settings: Settings,
    sentiment: SentimentRunner | None = None,
) -> CompiledStateGraph[TurnState, None, TurnState, TurnState]:
    nodes = _Nodes(agents, llm, settings, sentiment)
    graph: StateGraph[TurnState, None, TurnState, TurnState] = StateGraph(TurnState)
    graph.add_node("understand", nodes.understand)
    graph.add_node("sentiment", nodes.sentiment)
    graph.add_node("decide", nodes.decide)
    graph.add_node("escalate", nodes.escalate)
    graph.add_node("clarify", nodes.clarify)
    graph.add_node("dispatch", nodes.dispatch)
    graph.add_node("run_agent", nodes.run_agent)
    graph.add_node("reply", nodes.reply)
    graph.add_edge(START, "understand")
    graph.add_edge(START, "sentiment")
    graph.add_edge(["understand", "sentiment"], "decide")  # waits for both
    graph.add_conditional_edges("decide", _after_decide, ["escalate", "clarify", "dispatch"])
    graph.add_edge("escalate", END)
    graph.add_edge("clarify", END)
    graph.add_conditional_edges("dispatch", _fan_out, ["run_agent", "reply"])
    graph.add_edge("run_agent", "dispatch")
    graph.add_edge("reply", END)
    return graph.compile()


def _after_decide(state: TurnState) -> str:
    if state.get("escalation"):
        return "escalate"
    return "clarify" if state.get("outcome") == "clarify" else "dispatch"


def _fan_out(state: TurnState) -> list[Send] | str:
    ready = ready_tasks(state.get("tasks", []), state.get("results", {}), state.get("skipped", []))
    if not ready:
        return "reply"
    return [Send("run_agent", TurnState(task=task, turn=state["turn"])) for task in ready]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


class _Nodes:
    def __init__(
        self,
        agents: AgentRunner,
        llm: LLMClient | None,
        settings: Settings,
        sentiment: SentimentRunner | None,
    ) -> None:
        self._agents = agents
        self._llm = llm
        self._settings = settings
        self._sentiment = sentiment

    async def understand(self, state: TurnState) -> TurnState:
        start = time.perf_counter()
        turn, text = state["turn"], state["text"]
        memory = state["memory"].model_copy(deep=True)
        notes: list[str] = []
        try:
            parsed = await self._understand(turn, memory, text) if self._llm else _offline(text)
        except (LLMError, UnderstandingError) as exc:
            # Without an understanding, still try to answer from the business's knowledge.
            log.warning("understanding_failed", error=str(exc)[:200])
            parsed, notes = _offline(text), [NOTE_UNDERSTANDING_FAILED]

        understanding = parsed.understanding
        update: TurnState = {
            "memory": memory,
            "understanding": understanding.model_dump(mode="json"),
            "timings": {"understand": _elapsed_ms(start)},
        }
        unclear = understanding.confidence < self._settings.orchestrator_min_confidence
        if understanding.clarification_question or unclear:
            return {**update, "outcome": "clarify"}

        tasks, plan_notes = plan(parsed, memory)
        memory.clarifications = 0
        return {**update, "tasks": tasks, "notes": notes + plan_notes}

    async def _understand(self, turn: TurnContext, memory: SessionMemory, text: str) -> Parsed:
        assert self._llm is not None  # noqa: S101  # only called when a model is configured
        messages: list[ChatMessage] = understand_messages(
            turn.config,
            turn.now_local,
            turn.history,
            memory.booking,
            memory.awaiting_confirmation,
            text,
        )
        for attempt in range(UNDERSTAND_ATTEMPTS):
            raw = await self._llm.complete(messages, json_mode=True, max_tokens=1500)
            try:
                return parse_understanding(raw)
            except UnderstandingError as exc:
                if attempt + 1 == UNDERSTAND_ATTEMPTS:
                    raise
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That was not valid ({str(exc)[:300]}). "
                        "Reply with the corrected JSON object only.",
                    },
                ]
        raise UnderstandingError("no attempts made")  # pragma: no cover

    async def sentiment(self, state: TurnState) -> TurnState:
        if self._sentiment is None:
            return {"sentiment": None}
        start = time.perf_counter()
        turn = state["turn"]
        task = SentimentSubtask(
            text=state["text"],
            negative_streak=state["memory"].negative_streak,
            escalation=turn.config.escalation,
        )
        try:
            result: AgentResult | None = await self._sentiment.run(turn.agent_context, task)
        except Exception:
            # Without a reading the turn goes on as if the customer were calm (NFR04-01).
            log.exception("sentiment_failed")
            result = None
        return {"sentiment": result, "timings": {"agent.sentiment": _elapsed_ms(start)}}

    async def decide(self, state: TurnState) -> TurnState:
        memory = state["memory"]
        result = state.get("sentiment")
        if result is None:
            return {}
        memory.negative_streak = int(result.data.get("negative_streak", 0))
        reasons = list(result.data.get("reasons", []))
        if reasons:
            return {"memory": memory, "escalation": reasons}
        if result.data.get("complaint"):
            return {"memory": memory, "notes": [*state.get("notes", []), NOTE_UPSET]}
        return {"memory": memory}

    async def escalate(self, state: TurnState) -> TurnState:
        # Hand the conversation to a person (FR-06-04); the service records and notifies.
        memory = state["memory"]
        memory.handed_off = True
        return {
            "memory": memory,
            "outcome": "handoff",
            "reply": state["turn"].config.escalation.handoff_message,
        }

    async def clarify(self, state: TurnState) -> TurnState:
        memory = state["memory"]
        escalation = state["turn"].config.escalation
        if memory.clarifications >= escalation.max_clarification_attempts:
            # Still unclear after several tries: hand over to a person (FR-03-05, FR-06-04).
            memory.handed_off = True
            return {
                "memory": memory,
                "outcome": "handoff",
                "reply": escalation.handoff_message,
                "escalation": [REASON_UNCLEAR],
            }
        memory.clarifications += 1
        question = (state.get("understanding") or {}).get("clarification_question")
        return {"memory": memory, "outcome": "clarify", "reply": question or UNCLEAR}

    async def dispatch(self, state: TurnState) -> TurnState:
        tasks, results = state.get("tasks", []), state.get("results", {})
        skipped = list(state.get("skipped", []))
        newly: list[str] = []
        # Skipping one task can block the tasks that depend on it, so repeat until stable.
        while blocked := blocked_tasks(tasks, results, skipped):
            newly += blocked
            skipped += blocked
        return {"skipped": newly} if newly else {}

    async def run_agent(self, state: TurnState) -> TurnState:
        start = time.perf_counter()
        task = state["task"]
        try:
            result = await self._agents.run(task, state["turn"].agent_context)
        except Exception:
            # One failing agent mustn't sink the whole reply (NFR04-01).
            log.exception("agent_failed", agent=task.agent, task_id=task.id)
            result = AgentResult(
                agent=task.agent,
                status=AgentStatus.REJECTED,
                message="That couldn't be done right now. Please try again shortly.",
            )
        return {
            "results": {task.id: result},
            "timings": {f"agent.{task.agent}.{task.id}": _elapsed_ms(start)},
        }

    async def reply(self, state: TurnState) -> TurnState:
        start = time.perf_counter()
        turn, memory = state["turn"], state["memory"]
        results = state.get("results", {})
        tasks = state.get("tasks", [])
        for task in tasks:
            if task.booking is not None and task.id in results:
                remember_result(memory, task.booking, results[task.id])
        ordered = [(task.id, results.get(task.id)) for task in tasks]
        notes = state.get("notes", [])

        if self._llm is None:
            text = template_reply(turn.config, ordered, notes)
        else:
            try:
                text = await self._llm.complete(
                    reply_messages(turn.config, state["text"], ordered, notes),
                    temperature=0.3,
                    max_tokens=800,
                )
            except LLMError:
                text = template_reply(turn.config, ordered, notes)
        return {
            "memory": memory,
            "outcome": "answered",
            "reply": text,
            "timings": {"reply": _elapsed_ms(start)},
        }


def _offline(text: str) -> Parsed:
    """Without a model, treat every message as a question for the Knowledge agent."""
    question = KnowledgeSubtask(question=text[:1000])
    return Parsed(Understanding(), [Task("k1", "knowledge", knowledge=question)])
