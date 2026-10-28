"""The comparative report (TC-10 step 3, FR-10-06): Markdown for people, CSV and JSON for
further analysis."""

import csv
import io
from collections.abc import Callable

from app.evaluation.adjudication import Adjudication
from app.evaluation.human import HumanSummary
from app.evaluation.metrics import (
    METRICS,
    RATES,
    Metrics,
    Spread,
    across_repetitions,
    compute,
    grouped,
)
from app.evaluation.results import EvaluationRun, ScenarioRun, SystemInfo
from app.evaluation.scenarios import CATEGORIES

# The objectives' metrics (Report O7: task completion, response accuracy, response time;
# FR-10-03 knowledge accuracy), used for the "better on N of M" line.
PRIMARY = ("task_completion", "knowledge_accuracy", "response_accuracy", "latency_mean_ms")


def _format(name: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if name in RATES:
        return f"{value * 100:.1f}%"
    if name.endswith("_ms"):
        return f"{value:,.0f}"
    return f"{value:,.1f}"


def _format_spread(name: str, value: Spread) -> str:
    text = _format(name, value.mean)
    if value.sd is not None:
        sd = f"{value.sd * 100:.1f}" if name in RATES else f"{value.sd:,.0f}"
        text += f" ± {sd}"
    return text


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _runs_of(run: EvaluationRun, system: str) -> list[ScenarioRun]:
    return [r for r in run.runs if r.system == system]


def _better(name: str, ours: float | None, theirs: float | None) -> bool | None:
    if ours is None or theirs is None:
        return None
    higher_is_better = METRICS[name][1]
    return ours > theirs if higher_is_better else ours < theirs


def render_markdown(
    run: EvaluationRun,
    adjudications: list[Adjudication] | None = None,
    human: HumanSummary | None = None,
) -> str:
    adjudications = adjudications or []
    systems = [s for s in run.systems if s.skipped is None]
    spreads = {s.name: across_repetitions(_runs_of(run, s.name)) for s in systems}
    out: list[str] = ["# AgentIQ evaluation report", ""]

    domains = sorted({s.domain for s in run.scenarios})
    finished = run.finished_at.strftime("%Y-%m-%d %H:%M UTC") if run.finished_at else "not finished"
    out += [
        f"- **Run:** {run.started_at:%Y-%m-%d %H:%M UTC} to {finished}",
        f"- **Scenarios:** {len(run.scenarios)} ({', '.join(domains)}), "
        f"each run {run.repetitions} time(s) per system",
    ]
    out += [f"- **{key}:** {value}" for key, value in run.environment.items()]
    for system in run.systems:
        if system.skipped:
            out.append(f"- **{system.label}:** not run ({system.skipped})")
    out.append("")

    out += ["## Results", ""]
    header = ["Metric", *(s.label for s in systems)] + (["Human role-play"] if human else [])
    rows = []
    for name, (label, _) in METRICS.items():
        row = [label, *(_format_spread(name, spreads[s.name][name]) for s in systems)]
        if human:
            human_value = {
                "task_completion": human.task_completion,
                "scenario_success": human.scenario_success,
                "est_call_seconds": human.call_seconds,
            }.get(name)
            row.append(_format(name, human_value))
        rows.append(row)
    out += [_table(header, rows), ""]
    if run.repetitions > 1:
        out += ["Values are the mean ± standard deviation over repetitions.", ""]

    ours = spreads.get("agentiq")
    if ours is not None:
        for system in systems:
            if system.name == "agentiq":
                continue
            wins = [
                METRICS[name][0]
                for name in PRIMARY
                if _better(name, ours[name].mean, spreads[system.name][name].mean)
            ]
            compared = [
                name
                for name in PRIMARY
                if ours[name].mean is not None and spreads[system.name][name].mean is not None
            ]
            listed = f": {', '.join(wins)}" if wins else ""
            out.append(
                f"- AgentIQ does better than the {system.label} on {len(wins)} of "
                f"{len(compared)} primary metrics{listed}."
            )
        out.append("")

    out += _breakdown(run, systems, "By domain", lambda r: r.domain, domains)
    out += _breakdown(run, systems, "By category", lambda r: r.category, list(CATEGORIES))
    out += _scenario_matrix(run, systems)
    out += _failures(run, systems)

    if adjudications:
        out += ["## Adjudications", ""]
        out += [
            f"- {a.system} {a.scenario} `{a.id}` set to "
            f"{'correct' if a.correct else 'wrong'}: {a.note}"
            for a in adjudications
        ]
        out.append("")
    if human:
        out += [
            "## Human baseline",
            "",
            f"{human.rows} role-play calls (protocol in eval/README.md). Only task completion, "
            "scenario success and call length are recorded for people.",
            "",
        ]
    out += [
        "## Method",
        "",
        "- Every system plays the same scripted callers against a fresh copy of the example "
        "profile, with the clock fixed to the scenario's start (ADR-0009, eval/README.md).",
        "- Task completion counts expectations met; response accuracy adds the guard checks "
        "(every message answered, no unwanted booking, no needless hand-off).",
        "- Response time is measured around each reply. The IVR's replies are recorded "
        "prompts, so its response time is near zero; compare call length instead.",
        "- Judgments are rule-based; any evaluator override is listed under Adjudications.",
        "",
    ]
    return "\n".join(out)


def _breakdown(
    run: EvaluationRun,
    systems: list[SystemInfo],
    title: str,
    key: Callable[[ScenarioRun], str],
    order: list[str],
) -> list[str]:
    tables = {s.name: grouped(_runs_of(run, s.name), key) for s in systems}
    present = [name for name in order if any(name in t for t in tables.values())]
    header = [
        title.removeprefix("By ").capitalize(),
        *(f"{s.label}: tasks / accuracy" for s in systems),
    ]
    rows = []
    for name in present:
        cells = [name.replace("_", " ")]
        for system in systems:
            metrics: Metrics | None = tables[system.name].get(name)
            cells.append(
                "n/a"
                if metrics is None
                else f"{_format('task_completion', metrics.task_completion)} / "
                f"{_format('response_accuracy', metrics.response_accuracy)}"
            )
        rows.append(cells)
    return [f"## {title}", "", _table(header, rows), ""]


def _scenario_matrix(run: EvaluationRun, systems: list[SystemInfo]) -> list[str]:
    header = ["Scenario", *(s.label for s in systems)]
    rows = []
    for scenario in run.scenarios:
        cells = [f"{scenario.id} {scenario.title}"]
        for system in systems:
            runs = [r for r in _runs_of(run, system.name) if r.scenario_id == scenario.id]
            if not runs:
                cells.append("n/a")
                continue
            passed = sum(all(j.correct for j in r.judgments) for r in runs)
            met = sum(sum(j.correct for j in r.judgments) for r in runs)
            total = sum(len(r.judgments) for r in runs)
            mark = "✓" if passed == len(runs) else ("✗" if passed == 0 else "~")
            detail = f"{met}/{total}" if len(runs) == 1 else f"{passed}/{len(runs)} runs"
            cells.append(f"{mark} {detail}")
        rows.append(cells)
    return [
        "## Scenarios",
        "",
        "✓ every expectation met, ✗ not, ~ in some runs only.",
        "",
        _table(header, rows),
        "",
    ]


def _failures(run: EvaluationRun, systems: list[SystemInfo]) -> list[str]:
    out = ["## What went wrong", ""]
    any_failure = False
    for system in systems:
        lines = []
        for scenario_run in _runs_of(run, system.name):
            rep = f" (run {scenario_run.repetition})" if run.repetitions > 1 else ""
            if scenario_run.transcript.error:
                lines.append(
                    f"- {scenario_run.scenario_id}{rep}: error: {scenario_run.transcript.error}"
                )
            for check in [*scenario_run.judgments, *scenario_run.guards]:
                if not check.correct:
                    lines.append(f"- {scenario_run.scenario_id}{rep} `{check.id}`: {check.detail}")
        if lines:
            any_failure = True
            out += [f"### {system.label}", "", *lines, ""]
    if not any_failure:
        out += ["Nothing: every check passed.", ""]
    return out


def summary_csv(run: EvaluationRun) -> str:
    """One row per system and repetition, every metric as a number."""
    buffer = io.StringIO()
    fields = ["system", "repetition", *Metrics.model_fields]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for system in run.systems:
        if system.skipped:
            continue
        per_repetition = grouped(_runs_of(run, system.name), lambda r: str(r.repetition))
        for repetition, metrics in sorted(per_repetition.items(), key=lambda item: int(item[0])):
            writer.writerow(
                {"system": system.name, "repetition": repetition, **metrics.model_dump()}
            )
    return buffer.getvalue()


def overall(run: EvaluationRun, system: str) -> Metrics:
    return compute(_runs_of(run, system))
