"""Evaluator overrides (UC-10 alternative path: "the evaluator manually adjudicates and
documents the judgment call"). Kept in a YAML file and applied when the report is built,
so the automatic judgments in results.json are never edited by hand."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from app.evaluation.results import EvaluationRun


class Adjudication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str
    scenario: str
    id: str  # an expectation id, or a guard: replied, no-extra-booking, no-needless-handoff
    correct: bool
    note: str
    repetition: int | None = None  # every repetition when left out


def load_adjudications(path: Path) -> list[Adjudication]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [Adjudication.model_validate(item) for item in raw]


def apply_adjudications(run: EvaluationRun, items: list[Adjudication]) -> list[Adjudication]:
    """Applies each override in place; returns the ones that matched nothing."""
    unmatched = []
    for item in items:
        hit = False
        for scenario_run in run.runs:
            if (scenario_run.system, scenario_run.scenario_id) != (item.system, item.scenario):
                continue
            if item.repetition is not None and scenario_run.repetition != item.repetition:
                continue
            for judgment in [*scenario_run.judgments, *scenario_run.guards]:
                if judgment.id == item.id:
                    judgment.correct = item.correct
                    judgment.adjudicated = True
                    judgment.note = item.note
                    hit = True
        if not hit:
            unmatched.append(item)
    return unmatched
