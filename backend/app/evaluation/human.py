"""Results of the human-agent baseline (FR-10-06; analysis Q-5).

The human baseline is a timed role-play run by the team (protocol in eval/README.md): a
person playing the business's staff handles the same scenarios, and the evaluator records
one CSV row per scenario. The harness only reads that CSV into the report."""

import csv
import statistics
from pathlib import Path

from pydantic import BaseModel, Field


class HumanRow(BaseModel):
    scenario_id: str
    participant: str
    completed: int = Field(ge=0)  # expectations met
    total: int = Field(ge=1)  # expectations in the scenario
    call_seconds: float = Field(ge=0)


class HumanSummary(BaseModel):
    rows: int
    task_completion: float
    scenario_success: float
    call_seconds: float


def load_human_results(path: Path) -> list[HumanRow]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [HumanRow.model_validate(row) for row in csv.DictReader(handle)]


def summarize_human(rows: list[HumanRow]) -> HumanSummary | None:
    if not rows:
        return None
    return HumanSummary(
        rows=len(rows),
        task_completion=sum(r.completed for r in rows) / sum(r.total for r in rows),
        scenario_success=sum(r.completed == r.total for r in rows) / len(rows),
        call_seconds=statistics.fmean(r.call_seconds for r in rows),
    )
