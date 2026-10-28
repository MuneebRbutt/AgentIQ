"""What every evaluated system gets for one scenario run: a fresh copy of the domain's
example profile (with the evaluation documents), a clock fixed to the scenario's start, and
the shared database and retrieval pieces (ADR-0009)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.evaluation.results import Transcript
from app.evaluation.scenarios import Scenario
from app.knowledge.embeddings import Embedder
from app.knowledge.profile_facts import ProfileFactsIndex
from app.knowledge.store import ChromaVectorStore
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig

# How much time passes per customer message, so timestamps move like a real call.
TURN_SECONDS = 20
WORDS_PER_SECOND = 2.5  # speech at 150 words a minute


class ScenarioClock:
    """Starts at the scenario's start and only moves when told to."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def scenario_start(scenario: Scenario, config: BusinessProfileConfig) -> datetime:
    return scenario.start.replace(tzinfo=ZoneInfo(config.timezone)).astimezone(UTC)


def spoken_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND


@dataclass
class Shared:
    """Built once per run and shared by every scenario."""

    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    store: ChromaVectorStore
    embedder: Embedder
    facts: ProfileFactsIndex


@dataclass
class ScenarioEnv:
    shared: Shared
    tenant_id: uuid.UUID
    profile: BusinessProfile
    config: BusinessProfileConfig
    clock: ScenarioClock
    reference: str | None = None  # the setup booking's reference, if any
    notes: list[str] = field(default_factory=list)

    def turns(self, scenario: Scenario) -> list[str]:
        """The scripted messages, with the setup booking's reference filled in."""
        return [turn.replace("{reference}", self.reference or "") for turn in scenario.turns]


class EvaluatedSystem(Protocol):
    """AgentIQ or a baseline: plays one scenario and returns what was said (FR-10-06)."""

    name: str
    label: str

    async def run(self, scenario: Scenario, env: ScenarioEnv) -> Transcript: ...
