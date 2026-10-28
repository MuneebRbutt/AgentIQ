"""AgentIQ as an evaluated system: the real orchestrator and agents, called through the
same entry point as every channel (FR-10-06, CLAUDE.md §3)."""

import uuid

from app.agents.sentiment.agent import SentimentAgent
from app.evaluation.caller import ChatReply, converse
from app.evaluation.environment import ScenarioEnv
from app.evaluation.results import Transcript
from app.evaluation.scenarios import Scenario
from app.orchestrator.service import Agents, NormalizedRequest, Orchestrator
from app.providers.llm import LLMClient
from app.providers.notifications import LogNotifier
from app.providers.sentiment import SentimentClassifier


class AgentIQSystem:
    name = "agentiq"
    label = "AgentIQ (multi-agent)"

    def __init__(self, llm: LLMClient | None, classifier: SentimentClassifier) -> None:
        self._llm = llm
        self._classifier = classifier

    async def run(self, scenario: Scenario, env: ScenarioEnv) -> Transcript:
        shared = env.shared
        settings = shared.settings
        agents = Agents(
            shared.session_factory,
            env.clock,
            settings,
            shared.store,
            shared.embedder,
            shared.facts,
            self._llm,
        )
        sentiment = SentimentAgent(self._classifier, settings.sentiment_min_confidence)
        # Escalation notices are only logged: an evaluation never messages anyone.
        orchestrator = Orchestrator(
            shared.session_factory, env.clock, settings, agents, self._llm, sentiment, LogNotifier()
        )
        transcript = Transcript()
        await converse(
            _Session(orchestrator, env, scenario.caller.phone), scenario, env, transcript
        )
        return transcript


class _Session:
    def __init__(self, orchestrator: Orchestrator, env: ScenarioEnv, phone: str) -> None:
        self._orchestrator = orchestrator
        self._env = env
        self._phone = phone
        self._session_id: uuid.UUID | None = None

    async def send(self, text: str) -> ChatReply:
        outcome = await self._orchestrator.handle(
            NormalizedRequest(
                tenant_id=self._env.tenant_id,
                profile_id=self._env.profile.id,
                session_id=self._session_id,
                text=text,
                customer_phone=self._phone,
            )
        )
        self._session_id = outcome.session_id
        return ChatReply(text=outcome.reply, handed_off=outcome.handed_off)
