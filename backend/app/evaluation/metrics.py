"""The evaluation metrics (FR-10-01…04; analysis A-6, ADR-0009).

- Task completion: expectations met / expectations (FR-10-01). An expectation is one
  subtask of the scenario: an answer, a booking, a cancellation, a hand-off, a refusal.
- Scenario success: scenarios with every expectation met / scenarios.
- Knowledge accuracy: answer and refusal expectations met / those expectations (FR-10-03).
- Response accuracy: expectations plus guard checks met / all of them (FR-10-04). Guards
  catch what task completion can't: failed turns, unwanted bookings, needless hand-offs.
- Response time: time from the customer's message to the system's reply, over every reply
  (FR-10-02); and the estimated length of the whole call if spoken.
"""

import math
import statistics
from collections.abc import Callable, Iterable

from pydantic import BaseModel

from app.evaluation.results import ScenarioRun
from app.evaluation.scenarios import KNOWLEDGE_KINDS


class Metrics(BaseModel):
    scenarios: int
    expectations: int
    task_completion: float | None
    scenario_success: float | None
    knowledge_accuracy: float | None
    response_accuracy: float | None
    latency_mean_ms: float | None
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    est_call_seconds: float | None
    customer_turns: float | None
    errors: int


# Metric name → (label, higher is better). The order is the report's column order.
METRICS: dict[str, tuple[str, bool]] = {
    "task_completion": ("Task completion", True),
    "scenario_success": ("Scenario success", True),
    "knowledge_accuracy": ("Knowledge accuracy", True),
    "response_accuracy": ("Response accuracy", True),
    "latency_mean_ms": ("Mean response time (ms)", False),
    "latency_p95_ms": ("p95 response time (ms)", False),
    "est_call_seconds": ("Est. call length (s)", False),
}
RATES = {"task_completion", "scenario_success", "knowledge_accuracy", "response_accuracy"}


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear interpolation between closest ranks (the usual definition)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def compute(runs: Iterable[ScenarioRun]) -> Metrics:
    runs = list(runs)
    judgments = [j for run in runs for j in run.judgments]
    guards = [g for run in runs for g in run.guards]
    knowledge = [j for j in judgments if j.kind in KNOWLEDGE_KINDS]
    latencies = [
        line.latency_ms
        for run in runs
        for line in run.transcript.lines
        if line.speaker == "system" and line.latency_ms is not None
    ]
    checks = judgments + guards
    return Metrics(
        scenarios=len(runs),
        expectations=len(judgments),
        task_completion=_rate(sum(j.correct for j in judgments), len(judgments)),
        scenario_success=_rate(
            sum(all(j.correct for j in run.judgments) for run in runs), len(runs)
        ),
        knowledge_accuracy=_rate(sum(j.correct for j in knowledge), len(knowledge)),
        response_accuracy=_rate(sum(c.correct for c in checks), len(checks)),
        latency_mean_ms=statistics.fmean(latencies) if latencies else None,
        latency_p50_ms=percentile(latencies, 0.5),
        latency_p95_ms=percentile(latencies, 0.95),
        est_call_seconds=statistics.fmean(r.transcript.est_call_seconds for r in runs)
        if runs
        else None,
        customer_turns=statistics.fmean(
            sum(line.speaker == "customer" for line in r.transcript.lines) for r in runs
        )
        if runs
        else None,
        errors=sum(r.transcript.error is not None for r in runs),
    )


def grouped(runs: Iterable[ScenarioRun], key: Callable[[ScenarioRun], str]) -> dict[str, Metrics]:
    groups: dict[str, list[ScenarioRun]] = {}
    for run in runs:
        groups.setdefault(key(run), []).append(run)
    return {name: compute(members) for name, members in groups.items()}


class Spread(BaseModel):
    """A metric over repeated runs: mean and sample standard deviation."""

    mean: float | None
    sd: float | None
    n: int


def spread(values: list[float | None]) -> Spread:
    present = [v for v in values if v is not None]
    if not present:
        return Spread(mean=None, sd=None, n=0)
    sd = statistics.stdev(present) if len(present) > 1 else None
    return Spread(mean=statistics.fmean(present), sd=sd, n=len(present))


def across_repetitions(runs: list[ScenarioRun]) -> dict[str, Spread]:
    """Each metric computed per repetition, then summarised over repetitions."""
    per_repetition = grouped(runs, lambda run: str(run.repetition))
    return {
        name: spread([getattr(metrics, name) for metrics in per_repetition.values()])
        for name in METRICS
    }
