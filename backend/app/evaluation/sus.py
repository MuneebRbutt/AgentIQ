"""Scoring the usability study (FR-10-07, NFR02; analysis AS-15: SUS ≥ 68).

The System Usability Scale (Brooke, 1996) has ten statements answered 1 (strongly disagree)
to 5 (strongly agree). Odd statements are positive and score `answer - 1`; even ones are
negative and score `5 - answer`; the sum times 2.5 gives 0-100. 68 is the average across
hundreds of studies, so it is the "acceptable" line. Grades follow Sauro and Lewis's curved
scale (Quantifying the User Experience, 2nd ed., 2016).

    python -m app.cli sus-score eval/study/sus-responses.csv [--tasks eval/study/tasks.csv]

Responses CSV: `participant,q1,...,q10`. Tasks CSV: `participant,task,completed,seconds,
errors,assisted` (completed and assisted are yes/no). Templates are in eval/study/.
"""

import csv
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ACCEPTABLE = 68.0
QUESTIONS = [f"q{i}" for i in range(1, 11)]
# Lower bound of each grade (Sauro and Lewis 2016, table 8.5).
GRADES = [
    (84.1, "A+"),
    (80.8, "A"),
    (78.9, "A-"),
    (77.2, "B+"),
    (74.1, "B"),
    (72.6, "B-"),
    (71.1, "C+"),
    (65.0, "C"),
    (62.7, "C-"),
    (51.7, "D"),
    (0.0, "F"),
]
# Two-sided 95% t critical values by degrees of freedom; 1.96 beyond 30.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
    9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
    17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
    25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}  # fmt: skip


class StudyDataError(ValueError):
    """A row of the study's CSV can't be scored; the message says which and why."""


def sus_score(answers: list[int]) -> float:
    if len(answers) != len(QUESTIONS):
        raise StudyDataError(f"expected {len(QUESTIONS)} answers, got {len(answers)}")
    if any(a not in range(1, 6) for a in answers):
        raise StudyDataError("answers must be whole numbers from 1 to 5")
    total = sum(a - 1 if i % 2 == 0 else 5 - a for i, a in enumerate(answers))
    return total * 2.5


def grade(score: float) -> str:
    return next(letter for floor, letter in GRADES if score >= floor)


@dataclass(frozen=True, slots=True)
class SusSummary:
    scores: dict[str, float]  # participant -> score
    mean: float
    median: float
    sd: float | None  # None with one participant
    ci95: tuple[float, float] | None

    @property
    def acceptable(self) -> bool:
        return self.mean >= ACCEPTABLE


def summarize(scores: dict[str, float]) -> SusSummary:
    if not scores:
        raise StudyDataError("no responses")
    values = list(scores.values())
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else None
    ci = None
    if sd is not None:
        margin = _T95.get(len(values) - 1, 1.96) * sd / math.sqrt(len(values))
        ci = (max(0.0, mean - margin), min(100.0, mean + margin))
    return SusSummary(scores, mean, statistics.median(values), sd, ci)


def read_responses(path: Path) -> dict[str, float]:
    scores: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        for line, row in enumerate(csv.DictReader(file), start=2):
            participant = (row.get("participant") or "").strip()
            if not participant:
                raise StudyDataError(f"{path.name} line {line}: no participant")
            try:
                answers = [int((row.get(q) or "").strip()) for q in QUESTIONS]
                scores[participant] = sus_score(answers)
            except ValueError as exc:
                raise StudyDataError(f"{path.name} line {line} ({participant}): {exc}") from exc
    return scores


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task: str
    attempts: int
    completed: int
    assisted: int
    median_seconds: float | None
    mean_errors: float

    @property
    def completion_rate(self) -> float:
        return self.completed / self.attempts


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() in {"yes", "y", "true", "1"}


def read_tasks(path: Path) -> list[TaskSummary]:
    rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if (task := (row.get("task") or "").strip()) and (row.get("participant") or "").strip():
                rows[task].append(row)
    summaries = []
    for task, attempts in rows.items():
        seconds = [float(r["seconds"]) for r in attempts if _yes(r.get("completed"))]
        summaries.append(
            TaskSummary(
                task=task,
                attempts=len(attempts),
                completed=sum(_yes(r.get("completed")) for r in attempts),
                assisted=sum(_yes(r.get("assisted")) for r in attempts),
                median_seconds=statistics.median(seconds) if seconds else None,
                mean_errors=statistics.fmean(float(r.get("errors") or 0) for r in attempts),
            )
        )
    return summaries


def report(summary: SusSummary, tasks: list[TaskSummary] | None = None) -> str:
    """Markdown for the report's results chapter."""
    verdict = "meets" if summary.acceptable else "is below"
    lines = [
        "## System Usability Scale",
        "",
        f"{len(summary.scores)} participants. Mean SUS **{summary.mean:.1f}** "
        f"(grade {grade(summary.mean)}), which {verdict} the 68 target (AS-15).",
        "",
        "| Statistic | Value |",
        "|---|---|",
        f"| Mean | {summary.mean:.1f} |",
        f"| Median | {summary.median:.1f} |",
        f"| Standard deviation | {f'{summary.sd:.1f}' if summary.sd is not None else 'n/a'} |",
    ]
    if summary.ci95 is not None:
        low, high = summary.ci95
        lines.append(f"| 95% confidence interval | {low:.1f} to {high:.1f} |")
    lines += ["", "| Participant | SUS | Grade |", "|---|---|---|"]
    lines += [f"| {p} | {s:.1f} | {grade(s)} |" for p, s in summary.scores.items()]
    if tasks:
        lines += [
            "",
            "## Tasks",
            "",
            "| Task | Completed | Assisted | Median time (s) | Errors per attempt |",
            "|---|---|---|---|---|",
        ]
        for t in tasks:
            median = f"{t.median_seconds:.0f}" if t.median_seconds is not None else "n/a"
            lines.append(
                f"| {t.task} | {t.completed}/{t.attempts} ({t.completion_rate:.0%}) | "
                f"{t.assisted} | {median} | {t.mean_errors:.1f} |"
            )
    return "\n".join(lines) + "\n"
