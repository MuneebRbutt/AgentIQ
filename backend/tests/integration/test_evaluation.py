"""TC-10: the evaluation harness runs the same scenarios on AgentIQ, the single-agent baseline
and the IVR baseline against the real database, and produces a comparative report
(FR-10-01…06, ADR-0009). The models are scripted, so no paid API is called."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.models import Booking
from app.core.config import Settings
from app.evaluation.agentiq import AgentIQSystem
from app.evaluation.command import EVAL_DIR, write_outputs
from app.evaluation.environment import EvaluatedSystem, Shared
from app.evaluation.ivr import IvrSystem
from app.evaluation.results import EvaluationRun
from app.evaluation.runner import RunOptions, run_evaluation
from app.evaluation.single_agent import SingleAgentSystem
from app.profiles.models import BusinessProfile
from app.providers.llm import ChatMessage
from app.providers.sentiment import LexiconClassifier
from app.tenancy.models import Tenant

pytestmark = pytest.mark.integration

HALAL = "Yes, all our food is halal."
TABLE = {
    "action": "create",
    "service_id": "dine-in",
    "party_size": 4,
    "date": "2026-11-06",
    "time": "20:00",
    "customer_name": "Ali Raza",
}


class ScriptedLLM:
    """JSON calls get the next scripted item. Plain calls answer the knowledge question, or,
    for the orchestrator's reply step, repeat what the agents said."""

    def __init__(self, steps: list[dict[str, Any]]) -> None:
        self.steps = steps

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        json_mode: bool = False,
    ) -> str:
        if json_mode:
            return json.dumps(self.steps.pop(0))
        try:
            payload = json.loads(messages[-1]["content"])
        except ValueError:
            return HALAL
        if isinstance(payload, dict) and "results" in payload:
            return " ".join(r.get("message", "") for r in payload["results"])
        return HALAL

    async def aclose(self) -> None:
        return None


def _shared(app: FastAPI, settings: Settings) -> Shared:
    state = app.state
    return Shared(
        settings=settings,
        session_factory=state.session_factory,
        store=state.vector_store,
        embedder=state.embedder,
        facts=state.profile_facts,
    )


async def _evaluate(app: FastAPI, settings: Settings, keep_data: bool = False) -> EvaluationRun:
    agentiq_llm = ScriptedLLM(
        [
            {"subtasks": [{"id": "k1", "agent": "knowledge", "question": "Is the food halal?"}]},
            {"subtasks": [{"id": "b1", "agent": "booking", "booking": TABLE}]},
            {"confirmation": "yes"},
            {"subtasks": []},  # the complaint: sentiment decides, not the understanding
        ]
    )
    single_llm = ScriptedLLM(
        [
            {"action": "search", "query": "halal food"},
            {"action": "reply", "text": HALAL},
            {"action": "booking", "booking": TABLE | {"confirmed": True}},
            {"action": "reply", "text": "Your table for 4 is booked."},
            {"action": "handoff", "reason": "complaint"},
        ]
    )
    systems: list[EvaluatedSystem] = [
        AgentIQSystem(agentiq_llm, LexiconClassifier()),
        SingleAgentSystem(single_llm),
        IvrSystem(),
    ]
    options = RunOptions(scenario_ids={"R02", "R05", "R09"}, keep_data=keep_data)
    return await run_evaluation(_shared(app, settings), systems, EVAL_DIR, options)


async def test_tc10_every_system_runs_the_same_scenarios_and_is_scored(
    live_app: FastAPI, integration_settings: Settings, db: AsyncSession, tmp_path: Path
) -> None:
    run = await _evaluate(live_app, integration_settings)

    scores = {(r.system, r.scenario_id): [j.correct for j in r.judgments] for r in run.runs}
    # Halal question (R02), a table for four (R05), a complaint (R09).
    assert scores[("agentiq", "R02")] == [True]
    assert scores[("agentiq", "R05")] == [True]
    assert scores[("agentiq", "R09")] == [True]  # escalated on the complaint keywords
    assert scores[("single_agent", "R02")] == [True]
    assert scores[("single_agent", "R05")] == [True]
    assert scores[("single_agent", "R09")] == [True]
    assert scores[("ivr", "R02")] == [True]  # read from the policies menu
    assert scores[("ivr", "R05")] == [True]  # booked on the keypad
    assert scores[("ivr", "R09")] == [True]  # the caller pressed 0

    agentiq_booking = next(r for r in run.runs if (r.system, r.scenario_id) == ("agentiq", "R05"))
    assert [line.speaker for line in agentiq_booking.transcript.lines] == [
        "system", "customer", "system", "customer", "system"
    ]  # fmt: skip  # greeting, request, read-back, "yes", confirmation (FR-04-03)
    assert agentiq_booking.transcript.lines[3].text == "Yes, please go ahead."
    assert all(g.correct for g in agentiq_booking.guards)
    assert all(
        line.latency_ms is not None
        for line in agentiq_booking.transcript.lines[1:]
        if line.speaker == "system"
    )  # FR-10-02

    ivr_booking = next(r for r in run.runs if (r.system, r.scenario_id) == ("ivr", "R05"))
    assert ivr_booking.bookings[0].status == "confirmed"
    assert "[keys] 1" in ivr_booking.transcript.lines[1].text

    # The evaluation's business is removed afterwards (no data left behind).
    assert await db.scalar(select(func.count()).select_from(Tenant)) == 0
    assert await db.scalar(select(func.count()).select_from(Booking)) == 0

    # TC-10 step 3: the comparative report.
    report = write_outputs(run, tmp_path).read_text(encoding="utf-8")
    assert "| Task completion | 100.0% | 100.0% | 100.0% |" in report
    assert (tmp_path / "results.json").is_file()
    assert (tmp_path / "summary.csv").read_text(encoding="utf-8").count("\n") == 4


async def test_keep_data_leaves_the_evaluation_business_for_inspection(
    live_app: FastAPI, integration_settings: Settings, db: AsyncSession
) -> None:
    await _evaluate(live_app, integration_settings, keep_data=True)

    [tenant] = await db.scalars(select(Tenant))
    assert tenant.name.startswith("AgentIQ evaluation")
    profiles = await db.scalar(
        select(func.count())
        .select_from(BusinessProfile)
        .where(BusinessProfile.tenant_id == tenant.id)
    )
    assert profiles == 9  # one fresh profile per system and scenario
