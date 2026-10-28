"""The evaluation's pure parts: scenario files, scoring, metrics, the IVR menu, the simulated
caller and the report (FR-10-01…06, ADR-0009). The full harness runs in
tests/integration/test_evaluation.py."""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app import cli
from app.evaluation.adjudication import Adjudication, apply_adjudications, load_adjudications
from app.evaluation.caller import YES, details_line, follow_up
from app.evaluation.command import EVAL_DIR
from app.evaluation.human import load_human_results, summarize_human
from app.evaluation.ivr import main_menu, plan, topic_message
from app.evaluation.metrics import across_repetitions, compute, percentile
from app.evaluation.report import render_markdown, summary_csv
from app.evaluation.results import (
    BookingEvidence,
    EvaluationRun,
    Judgment,
    ScenarioInfo,
    ScenarioRun,
    SystemInfo,
    Transcript,
)
from app.evaluation.scenarios import (
    CATEGORIES,
    DomainSet,
    Scenario,
    ScenarioFileError,
    check_against_profile,
    load_scenarios,
)
from app.evaluation.scoring import judge, mentions
from app.evaluation.single_agent import parse_step
from app.profiles.examples import load_example
from app.profiles.schema import Domain

START = dt.datetime(2026, 11, 2, 11, 0)


def _scenario(expect: list[dict[str, Any]], **fields: Any) -> Scenario:
    return Scenario.model_validate(
        {
            "id": "T01",
            "title": "Test",
            "category": "knowledge",
            "start": START,
            "caller": {"name": "Ali Raza", "phone": "+92 300 5550101"},
            "turns": ["Hello"],
            "expect": expect,
            **fields,
        }
    )


def _transcript(*replies: str, handed_off: bool = False) -> Transcript:
    transcript = Transcript(handed_off=handed_off)
    for reply in replies:
        transcript.say("customer", "question")
        transcript.say("system", reply, latency_ms=100.0)
    return transcript


def _booking(**fields: Any) -> BookingEvidence:
    values: dict[str, Any] = {
        "reference": "ABC123",
        "service_id": "dine-in",
        "party_size": 4,
        "start_local": dt.datetime(2026, 11, 6, 20, 0),
        "nights": None,
        "status": "confirmed",
        "setup": False,
    }
    return BookingEvidence(**(values | fields))


TABLE = {
    "kind": "booking",
    "id": "table",
    "service_id": "dine-in",
    "party_size": 4,
    "date": "2026-11-06",
    "time": "20:00",
}


# Scenario files


def test_scenario_files_load_and_fit_their_profiles() -> None:
    """FR-10-01: a scenario set per domain, every one checked against its profile."""
    sets = load_scenarios(EVAL_DIR)

    assert {s.domain for s in sets} == {Domain.HOTEL, Domain.RESTAURANT, Domain.CLINIC}
    for domain_set in sets:
        assert len(domain_set.scenarios) >= 10
        assert {s.category for s in domain_set.scenarios} == set(CATEGORIES)


def test_scenario_rejects_a_time_zone_and_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="local time"):
        _scenario([TABLE], start="2026-11-02T11:00:00+05:00")
    with pytest.raises(ValidationError, match="unique"):
        _scenario([TABLE, TABLE])


def test_a_reference_needs_a_setup_booking() -> None:
    with pytest.raises(ValidationError, match="setup_booking"):
        _scenario([{"kind": "cancelled", "id": "c"}], turns=["Cancel {reference}"])


def test_refusal_patterns_must_compile() -> None:
    with pytest.raises(ValidationError):
        _scenario([{"kind": "refusal", "id": "r", "forbid_patterns": ["(unclosed"]}])


@pytest.mark.parametrize(
    ("domain", "booking", "caller", "error"),
    [
        (Domain.RESTAURANT, TABLE | {"service_id": "spa"}, {}, "unknown service"),
        (Domain.RESTAURANT, TABLE | {"time": None, "nights": 2}, {}, "by time"),
        (Domain.HOTEL, TABLE | {"service_id": "room-stay"}, {"email": "a@x.com"}, "by night"),
        (
            Domain.CLINIC,
            TABLE | {"service_id": "general-consultation", "party_size": 1},
            {},
            "needs details",
        ),
        (Domain.RESTAURANT, TABLE, {"details": {"shoe_size": "9"}}, "unknown caller details"),
    ],
)
def test_ground_truth_is_checked_against_the_profile(
    domain: Domain, booking: dict[str, Any], caller: dict[str, Any], error: str
) -> None:
    scenario = _scenario(
        [booking], caller={"name": "Ali Raza", "phone": "+92 300 5550101", **caller}
    )
    domain_set = DomainSet(domain=domain, scenarios=[scenario])

    with pytest.raises(ScenarioFileError, match=error):
        check_against_profile(domain_set, load_example(domain), EVAL_DIR)


def test_a_missing_document_is_reported(tmp_path: Path) -> None:
    domain_set = DomainSet(
        domain=Domain.RESTAURANT, documents=["nope.md"], scenarios=[_scenario([TABLE])]
    )

    with pytest.raises(ScenarioFileError, match=r"nope\.md"):
        check_against_profile(domain_set, load_example(Domain.RESTAURANT), tmp_path)


# Scoring


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [
        ("We open at 9:00 AM.", "9:00", True),
        ("We close at 19:00.", "9:00", False),
        ("It costs PKR 2,400.", "2,400", True),
        ("It costs PKR 12,400.", "2,400", False),
        ("Wi\u2011Fi is FREE for guests", "free", True),
        ("We don\u2019t know", "don't know", True),
    ],
)
def test_mentions_matches_whole_words(text: str, phrase: str, found: bool) -> None:
    assert mentions(text, phrase) is found


def test_answer_needs_every_fact_in_any_form_and_no_forbidden_one() -> None:
    expect = [
        {
            "kind": "answer",
            "id": "hours",
            "facts": [["12:00", "noon"], ["00:30", "12:30 am"]],
            "forbid": ["23:00"],
        }
    ]
    scenario = _scenario(expect)

    [ok], _ = judge(scenario, _transcript("We open at noon and close at 12:30 AM."), [])
    [missing], _ = judge(scenario, _transcript("We open at noon."), [])
    [wrong], _ = judge(scenario, _transcript("Noon to 00:30, kitchen until 23:00."), [])

    assert ok.correct
    assert not missing.correct and "00:30" in missing.detail
    assert not wrong.correct and "23:00" in wrong.detail


def test_refusal_must_decline_without_inventing_an_answer() -> None:
    """FR-05-05: out-of-scope questions are declined, not answered from thin air."""
    scenario = _scenario(
        [{"kind": "refusal", "id": "r", "forbid_patterns": [r"(?i)\bpkr\s*\d[\d,]*"]}]
    )

    [declined], _ = judge(scenario, _transcript("Sorry, I don't have information about that."), [])
    [invented], _ = judge(scenario, _transcript("I'm not sure, maybe PKR 5,000."), [])
    [silent], _ = judge(scenario, _transcript("We have a lovely garden."), [])
    [passed_on], _ = judge(scenario, _transcript("One moment.", handed_off=True), [])

    assert declined.correct and passed_on.correct
    assert not invented.correct and not silent.correct


def test_booking_must_match_and_extras_are_caught() -> None:
    scenario = _scenario([TABLE])

    [hit], guards = judge(scenario, _transcript("Booked."), [_booking()])
    assert hit.correct and all(g.correct for g in guards)

    wrong_time = _booking(start_local=dt.datetime(2026, 11, 6, 19, 0))
    [miss], guards = judge(scenario, _transcript("Booked."), [wrong_time])
    assert not miss.correct and "wrong booking" in miss.detail
    assert not next(g for g in guards if g.id == "no-extra-booking").correct

    held = _booking(status="held")
    [none], _ = judge(scenario, _transcript("Shall I book it?"), [held])
    assert not none.correct and none.detail == "no booking made"


def test_overnight_bookings_match_on_nights() -> None:
    room = {
        "kind": "booking",
        "id": "room",
        "service_id": "standard-room",
        "party_size": 2,
        "date": "2026-11-09",
        "nights": 2,
    }
    evidence = _booking(
        service_id="standard-room",
        party_size=2,
        start_local=dt.datetime(2026, 11, 9, 14, 0),
        nights=2,
    )

    [hit], _ = judge(_scenario([room]), _transcript("Booked."), [evidence])
    [miss], _ = judge(
        _scenario([room]), _transcript("Booked."), [evidence.model_copy(update={"nights": 3})]
    )

    assert hit.correct and not miss.correct


def test_cancellation_and_escalation() -> None:
    setup = {"service_id": "dine-in", "party_size": 2, "date": "2026-11-06", "time": "20:00"}
    cancel = _scenario([{"kind": "cancelled", "id": "c"}], setup_booking=setup)
    escalate = _scenario([{"kind": "escalation", "id": "e"}])

    [done], _ = judge(cancel, _transcript("Cancelled."), [_booking(setup=True, status="cancelled")])
    [kept], _ = judge(cancel, _transcript("Sorry."), [_booking(setup=True)])
    [handed], guards = judge(escalate, _transcript("A person will call.", handed_off=True), [])
    [kept_talking], _ = judge(escalate, _transcript("I see."), [])

    assert done.correct and not kept.correct
    assert handed.correct and all(g.correct for g in guards)
    assert not kept_talking.correct


def test_guards_catch_unanswered_turns_errors_and_needless_handoffs() -> None:
    scenario = _scenario([TABLE])
    unanswered = _transcript("Booked.")
    unanswered.say("customer", "Hello?")

    _, guards = judge(scenario, unanswered, [_booking()])
    assert not next(g for g in guards if g.id == "replied").correct

    _, guards = judge(scenario, Transcript(error="TimeoutError: slow"), [_booking()])
    replied = next(g for g in guards if g.id == "replied")
    assert not replied.correct and "Timeout" in replied.detail

    _, guards = judge(scenario, _transcript("Hold on.", handed_off=True), [_booking()])
    assert not next(g for g in guards if g.id == "no-needless-handoff").correct


# Metrics


def test_percentile_interpolates() -> None:
    assert percentile([], 0.5) is None
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([0.0, 100.0], 0.95) == pytest.approx(95.0)


def _run(
    system: str,
    scenario: str,
    results: list[bool],
    *,
    kind: str = "answer",
    repetition: int = 1,
    latency: float = 100.0,
    category: str = "knowledge",
) -> ScenarioRun:
    transcript = Transcript(est_call_seconds=30.0)
    transcript.say("customer", "question")
    transcript.say("system", "answer", latency_ms=latency)
    return ScenarioRun(
        system=system,
        scenario_id=scenario,
        domain="restaurant",
        category=category,
        repetition=repetition,
        transcript=transcript,
        judgments=[
            Judgment(id=f"e{i}", kind=kind, correct=ok, detail="") for i, ok in enumerate(results)
        ],
        guards=[Judgment(id="replied", kind="guard", correct=True, detail="")],
    )


def test_metrics_follow_their_definitions() -> None:
    runs = [
        _run("agentiq", "R01", [True, True]),
        _run("agentiq", "R02", [True, False], kind="booking", latency=300.0),
    ]

    metrics = compute(runs)

    assert metrics.task_completion == pytest.approx(3 / 4)
    assert metrics.scenario_success == pytest.approx(1 / 2)
    assert metrics.knowledge_accuracy == pytest.approx(1.0)  # only answer/refusal items
    assert metrics.response_accuracy == pytest.approx(5 / 6)  # plus the two guards
    assert metrics.latency_mean_ms == pytest.approx(200.0)
    assert metrics.est_call_seconds == pytest.approx(30.0)


def test_repetitions_give_mean_and_spread() -> None:
    runs = [
        _run("agentiq", "R01", [True], repetition=1),
        _run("agentiq", "R01", [False], repetition=2),
    ]

    spreads = across_repetitions(runs)

    assert spreads["task_completion"].mean == pytest.approx(0.5)
    assert spreads["task_completion"].sd == pytest.approx(0.7071, abs=1e-3)
    assert spreads["task_completion"].n == 2


# IVR baseline


def test_ivr_menu_and_topics_come_from_the_profile() -> None:
    config = load_example(Domain.RESTAURANT)

    assert config.name in main_menu(config)
    assert "press 0" in main_menu(config)
    assert "12:00" in topic_message(config, "2")  # opening hours
    assert config.contact.phone is not None and config.contact.phone in topic_message(config, "3")
    assert config.services[0].name in topic_message(config, "4")
    assert config.policies[0].text in topic_message(config, "5")


def test_ivr_plan_takes_topics_then_bookings_then_the_operator() -> None:
    scenario = _scenario(
        [
            TABLE,
            {"kind": "answer", "id": "menu", "facts": [["karahi"]]},  # in no menu
            {"kind": "answer", "id": "hours", "facts": [["12:00"]], "topic": "hours"},
        ]
    )

    goals = plan(scenario)

    assert [kind for kind, _ in goals] == ["topic", "booking", "operator"]
    assert goals[0] == ("topic", "2")


# Simulated caller


def test_caller_gives_details_once_then_confirms() -> None:
    config = load_example(Domain.CLINIC)
    caller = _scenario(
        [TABLE],
        caller={
            "name": "Fatima Khan",
            "phone": "+92 302 5550305",
            "details": {"patient_age": "34", "reason_for_visit": "Fever"},
        },
    ).caller

    asked = follow_up("May I have your name and phone number?", caller, config, False)
    asked_field = follow_up("What is the reason for visit?", caller, config, False)
    confirm = follow_up("Shall I confirm this appointment?", caller, config, True)

    assert asked == details_line(caller, config)
    assert asked is not None and "Fatima Khan" in asked and "Patient's age: 34" in asked
    assert asked_field == asked
    assert confirm == YES
    assert follow_up("Please confirm: table for 4 at 20:00.", caller, config, True) == YES
    assert follow_up("Your booking is confirmed.", caller, config, True) is None
    assert follow_up("Anything else?", caller, config, True) is None


# Single-agent baseline


def test_single_agent_steps_are_validated() -> None:
    assert parse_step('```json\n{"action": "reply", "text": "Hi"}\n```').text == "Hi"
    assert parse_step('{"action": "search", "query": "hours"}').query == "hours"
    with pytest.raises(ValueError, match="needs text"):
        parse_step('{"action": "reply", "text": " "}')
    with pytest.raises(ValueError, match="needs a query"):
        parse_step('{"action": "search"}')
    with pytest.raises(ValidationError):
        parse_step('{"action": "dance"}')


# Report, adjudication and the human baseline


def _evaluation() -> EvaluationRun:
    return EvaluationRun(
        started_at=dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 11, 2, 9, 5, tzinfo=dt.UTC),
        repetitions=1,
        environment={"Language model": "test-model"},
        systems=[
            SystemInfo(name="agentiq", label="AgentIQ (multi-agent)"),
            SystemInfo(name="ivr", label="IVR baseline (keypad menu)"),
            SystemInfo(name="single_agent", label="Single agent", skipped="no model"),
        ],
        scenarios=[
            ScenarioInfo(id="R01", domain="restaurant", category="knowledge", title="Hours"),
            ScenarioInfo(id="R05", domain="restaurant", category="booking", title="Table"),
        ],
        runs=[
            _run("agentiq", "R01", [True], latency=500.0),
            _run("agentiq", "R05", [True], kind="booking", category="booking", latency=500.0),
            _run("ivr", "R01", [True], latency=1.0),
            _run("ivr", "R05", [False], kind="booking", category="booking", latency=1.0),
        ],
    )


def test_report_compares_every_system_and_lists_failures() -> None:
    report = render_markdown(_evaluation())

    assert "| Task completion | 100.0% | 50.0% |" in report
    assert "Single agent:** not run (no model)" in report
    assert "AgentIQ does better than the IVR baseline (keypad menu) on 2 of 4" in report
    assert "| R05 Table | ✓ 1/1 | ✗ 0/1 |" in report
    assert "- R05 `e0`" in report  # under the IVR's failures
    assert "Language model:** test-model" in report


def test_summary_csv_has_a_row_per_system_and_repetition() -> None:
    rows = summary_csv(_evaluation()).splitlines()

    assert rows[0].startswith("system,repetition,scenarios,expectations,task_completion")
    assert [row.split(",")[0] for row in rows[1:]] == ["agentiq", "ivr"]


def test_adjudications_override_and_unmatched_ones_are_returned(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.yaml"
    path.write_text(
        "- {system: ivr, scenario: R05, id: e0, correct: true, note: Booked by the operator}\n"
        "- {system: ivr, scenario: R99, id: e0, correct: true, note: typo}\n",
        encoding="utf-8",
    )
    run = _evaluation()

    unmatched = apply_adjudications(run, load_adjudications(path))

    assert [a.scenario for a in unmatched] == ["R99"]
    judgment = next(r for r in run.runs if (r.system, r.scenario_id) == ("ivr", "R05")).judgments[0]
    assert judgment.correct and judgment.adjudicated and judgment.note == "Booked by the operator"
    with pytest.raises(ValidationError):
        Adjudication.model_validate({"system": "ivr", "scenario": "R05", "id": "e0"})


def test_human_results_are_summarised(tmp_path: Path) -> None:
    path = tmp_path / "human.csv"
    path.write_text(
        "scenario_id,participant,completed,total,call_seconds\nR01,P1,1,1,40\nR07,P1,1,2,80\n",
        encoding="utf-8",
    )

    summary = summarize_human(load_human_results(path))

    assert summary is not None
    assert summary.task_completion == pytest.approx(2 / 3)
    assert summary.scenario_success == pytest.approx(0.5)
    assert summary.call_seconds == pytest.approx(60.0)
    assert "Human role-play" in render_markdown(_evaluation(), human=summary)


def test_cli_parses_the_evaluation_commands() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(
        ["evaluate", "--system", "ivr", "--system", "agentiq", "--domain", "hotel", "--repeat", "3"]
    )
    rebuild = parser.parse_args(["evaluate-report", "--results", "r/results.json"])

    assert args.system == ["ivr", "agentiq"] and args.repeat == 3
    assert rebuild.results == Path("r/results.json")
    with pytest.raises(SystemExit):
        parser.parse_args(["evaluate", "--system", "chatgpt"])
