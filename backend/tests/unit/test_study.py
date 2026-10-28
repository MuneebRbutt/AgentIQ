"""Scoring the usability study (FR-10-07, NFR02) and getting a deployment ready (FR-10-08)."""

from pathlib import Path

import pytest

from app.cli import main
from app.core.config import Settings
from app.deployment import index_is_missing
from app.evaluation.sus import (
    StudyDataError,
    grade,
    read_responses,
    read_tasks,
    report,
    summarize,
    sus_score,
)


@pytest.mark.parametrize(
    ("answers", "score"),
    [
        ([5, 1, 5, 1, 5, 1, 5, 1, 5, 1], 100.0),  # best possible
        ([1, 5, 1, 5, 1, 5, 1, 5, 1, 5], 0.0),  # worst possible
        ([3] * 10, 50.0),
        ([4, 2, 4, 2, 4, 2, 4, 2, 4, 2], 75.0),
        ([5, 2, 4, 1, 4, 2, 5, 1, 4, 3], 82.5),
    ],
)
def test_sus_scores_odd_items_up_and_even_items_down(answers: list[int], score: float) -> None:
    assert sus_score(answers) == score


@pytest.mark.parametrize("answers", [[3] * 9, [0, 3, 3, 3, 3, 3, 3, 3, 3, 3], [6] * 10])
def test_impossible_answers_are_refused(answers: list[int]) -> None:
    with pytest.raises(StudyDataError):
        sus_score(answers)


@pytest.mark.parametrize(
    ("score", "letter"), [(100, "A+"), (84.1, "A+"), (80.8, "A"), (68, "C"), (51.7, "D"), (40, "F")]
)
def test_grades_follow_the_curved_scale(score: float, letter: str) -> None:
    assert grade(score) == letter


def test_a_summary_with_confidence_interval() -> None:
    summary = summarize({"P01": 70.0, "P02": 80.0, "P03": 75.0, "P04": 65.0})

    assert summary.mean == 72.5 and summary.median == 72.5
    assert summary.sd == pytest.approx(6.455, abs=0.001)
    low, high = summary.ci95 or (0, 0)
    assert (round(low, 1), round(high, 1)) == (62.2, 82.8)  # t(3) = 3.182
    assert summary.acceptable
    assert summarize({"P01": 60.0}).ci95 is None


def test_the_study_files_become_a_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = tmp_path / "sus.csv"
    responses.write_text(
        "participant,q1,q2,q3,q4,q5,q6,q7,q8,q9,q10\n"
        "P01,4,2,4,2,4,2,4,2,4,2\n"
        "P02,5,1,5,1,5,1,5,1,5,1\n"
    )
    tasks = tmp_path / "tasks.csv"
    tasks.write_text(
        "participant,task,completed,seconds,errors,assisted,notes\n"
        "P01,T1 Log in,yes,20,0,no,\n"
        "P02,T1 Log in,yes,40,1,no,\n"
        "P01,T2 Set up a profile,no,300,3,yes,gave up\n"
        "P02,T2 Set up a profile,yes,180,0,no,\n"
        "P03,T2 Set up a profile,,,,,\n"
    )

    assert read_responses(responses) == {"P01": 75.0, "P02": 100.0}
    [login, setup] = read_tasks(tasks)
    assert (login.completed, login.attempts, login.median_seconds) == (2, 2, 30.0)
    assert (setup.completed, setup.attempts, setup.assisted) == (1, 3, 1)
    text = report(summarize(read_responses(responses)), [login, setup])
    assert "Mean SUS **87.5** (grade A+), which meets the 68 target" in text
    assert "| T2 Set up a profile | 1/3 (33%) | 1 | 180 | 1.0 |" in text

    assert main(["sus-score", str(responses), "--tasks", str(tasks)]) == 0
    assert "## Tasks" in capsys.readouterr().out


def test_a_bad_row_is_named(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    responses = tmp_path / "sus.csv"
    responses.write_text("participant,q1,q2,q3,q4,q5,q6,q7,q8,q9,q10\nP01,4,2,4,2,4,2,4,2,4,\n")

    assert main(["sus-score", str(responses)]) == 1
    assert "sus.csv line 2 (P01)" in capsys.readouterr().err


def test_a_missing_or_empty_index_is_rebuilt(tmp_path: Path) -> None:
    def settings(path: str) -> Settings:
        return Settings(_env_file=None, chroma_path=path)

    assert index_is_missing(settings(""))  # in memory
    assert index_is_missing(settings(str(tmp_path / "chroma")))
    (tmp_path / "chroma").mkdir()
    assert index_is_missing(settings(str(tmp_path / "chroma")))
    (tmp_path / "chroma" / "chroma.sqlite3").write_bytes(b"")
    assert not index_is_missing(settings(str(tmp_path / "chroma")))
