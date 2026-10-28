"""Understanding validation, session memory and task planning (FR-03-02/03, FR-04-03)."""

import datetime as dt
from typing import Literal

import pytest

from app.agents.booking.agent import BookingAction, BookingSubtask
from app.agents.contracts import AgentResult, AgentStatus
from app.orchestrator.memory import SessionMemory, merge_booking, remember_result
from app.orchestrator.planning import NOTE_DECLINED, blocked_tasks, plan, ready_tasks
from app.orchestrator.understanding import (
    Parsed,
    Task,
    Understanding,
    UnderstandingError,
    parse_understanding,
)


def _parse(payload: str) -> Parsed:
    return parse_understanding(payload)


class TestParse:
    def test_valid_multi_intent_understanding(self) -> None:
        parsed = _parse(
            """```json
            {"subtasks": [
              {"id": "s1", "agent": "knowledge", "question": "When are you open?"},
              {"id": "s2", "agent": "booking", "depends_on": ["s1"],
               "booking": {"action": "create", "party_size": 4, "date": "2026-10-20",
                           "time": "19:00", "confirmed": true, "tenant_id": "x",
                           "customer_name": null}}],
             "confidence": 0.9, "extra": "ignored"}
            ```"""
        )
        knowledge, booking = parsed.tasks
        assert knowledge.knowledge is not None
        assert knowledge.knowledge.question == "When are you open?"
        assert booking.booking is not None
        assert booking.booking.party_size == 4
        assert booking.booking.time == dt.time(19, 0)
        # The model can't confirm a booking or set fields it doesn't own.
        assert booking.booking.confirmed is False
        assert booking.booking.model_fields_set == {"action", "party_size", "date", "time"}
        assert booking.depends_on == ("s1",)

    @pytest.mark.parametrize(
        "payload",
        [
            "not json",
            '{"subtasks": [{"id": "s1", "agent": "weather"}]}',
            '{"subtasks": [{"id": "s1", "agent": "knowledge"}]}',
            '{"subtasks": [{"id": "s1", "agent": "booking", "booking": {"party_size": 2}}]}',
            '{"subtasks": [{"id": "s1", "agent": "booking", '
            '"booking": {"action": "create", "party_size": -1}}]}',
            '{"confidence": 3}',
        ],
    )
    def test_unusable_output_is_rejected(self, payload: str) -> None:
        with pytest.raises(UnderstandingError):
            _parse(payload)

    def test_empty_understanding_is_small_talk(self) -> None:
        parsed = _parse("{}")
        assert parsed.tasks == []
        assert parsed.understanding.confidence == 1.0


def _booking(**fields: object) -> BookingSubtask:
    return BookingSubtask.model_validate(fields)


class TestMerge:
    def test_details_accumulate_over_turns(self) -> None:
        draft = merge_booking(None, _booking(action="create", party_size=4))
        draft = merge_booking(draft, _booking(action="create", date="2026-10-20", time="19:00"))
        draft = merge_booking(draft, _booking(action="create", details={"occasion": "birthday"}))
        draft = merge_booking(draft, _booking(action="create", details={"seating": "outside"}))
        assert draft.party_size == 4
        assert draft.date == dt.date(2026, 10, 20)
        assert draft.details == {"occasion": "birthday", "seating": "outside"}

    def test_a_later_value_replaces_an_earlier_one(self) -> None:
        draft = merge_booking(
            _booking(action="create", party_size=4), _booking(action="create", party_size=6)
        )
        assert draft.party_size == 6

    def test_asking_about_another_time_keeps_it_a_new_booking(self) -> None:
        draft = merge_booking(
            _booking(action="create", party_size=2), _booking(action="check", time="20:00")
        )
        assert draft.action is BookingAction.CREATE
        draft = merge_booking(_booking(action="check", party_size=2), _booking(action="create"))
        assert draft.action is BookingAction.CREATE and draft.party_size == 2

    def test_switching_to_changing_an_existing_booking_starts_over(self) -> None:
        draft = merge_booking(
            _booking(action="create", party_size=2), _booking(action="cancel", reference="ABC123")
        )
        assert draft.action is BookingAction.CANCEL
        assert draft.party_size is None

    def test_merged_draft_is_never_confirmed(self) -> None:
        draft = merge_booking(
            _booking(action="create", confirmed=True), _booking(action="create", confirmed=True)
        )
        assert draft.confirmed is False


def _result(status: AgentStatus) -> AgentResult:
    return AgentResult(agent="booking", status=status, message="m")


class TestRememberResult:
    def test_read_back_waits_for_confirmation(self) -> None:
        memory = SessionMemory()
        remember_result(
            memory, _booking(action="create", party_size=2), _result(AgentStatus.NEEDS_CONFIRMATION)
        )
        assert memory.awaiting_confirmation
        assert memory.draft() == _booking(action="create", party_size=2)

    def test_completed_booking_clears_the_draft(self) -> None:
        memory = SessionMemory(booking={"action": "create"}, awaiting_confirmation=True)
        remember_result(
            memory, _booking(action="create", confirmed=True), _result(AgentStatus.COMPLETED)
        )
        assert memory.booking is None
        assert not memory.awaiting_confirmation

    def test_a_completed_check_keeps_the_details(self) -> None:
        memory = SessionMemory()
        remember_result(
            memory, _booking(action="check", party_size=3), _result(AgentStatus.COMPLETED)
        )
        assert memory.booking is not None and memory.booking["party_size"] == 3

    def test_other_outcomes_keep_the_draft(self) -> None:
        memory = SessionMemory()
        remember_result(
            memory, _booking(action="create", party_size=3), _result(AgentStatus.UNAVAILABLE)
        )
        assert memory.booking is not None and not memory.awaiting_confirmation


def _parsed(*tasks: Task, confirmation: Literal["yes", "no"] | None = None) -> Parsed:
    return Parsed(Understanding(confirmation=confirmation), list(tasks))


class TestPlan:
    def test_yes_commits_exactly_what_was_read_back(self) -> None:
        memory = SessionMemory(
            booking={"action": "create", "party_size": 2}, awaiting_confirmation=True
        )
        tasks, notes = plan(_parsed(confirmation="yes"), memory)
        [task] = tasks
        assert task.booking is not None and task.booking.confirmed
        assert task.booking.party_size == 2
        assert notes == []

    def test_yes_without_a_read_back_confirms_nothing(self) -> None:
        memory = SessionMemory(booking={"action": "create", "party_size": 2})
        tasks, _ = plan(_parsed(confirmation="yes"), memory)
        assert tasks == []

    def test_yes_with_a_change_needs_a_new_read_back(self) -> None:
        memory = SessionMemory(
            booking={"action": "create", "party_size": 2}, awaiting_confirmation=True
        )
        update = Task("s1", "booking", booking=_booking(action="create", party_size=3))
        [task], _ = plan(_parsed(update, confirmation="yes"), memory)
        assert task.booking is not None and not task.booking.confirmed
        assert task.booking.party_size == 3
        assert memory.booking is not None and memory.booking["party_size"] == 3

    def test_no_is_noted_and_keeps_the_draft(self) -> None:
        memory = SessionMemory(booking={"action": "create"}, awaiting_confirmation=True)
        tasks, notes = plan(_parsed(confirmation="no"), memory)
        assert tasks == [] and notes == [NOTE_DECLINED]
        assert memory.booking is not None and not memory.awaiting_confirmation

    def test_several_booking_subtasks_become_one_and_unknown_dependencies_go(self) -> None:
        memory = SessionMemory()
        tasks, _ = plan(
            _parsed(
                Task("k1", "knowledge", depends_on=("k1", "nope")),
                Task("b1", "booking", booking=_booking(action="check", party_size=2)),
                Task(
                    "b2",
                    "booking",
                    booking=_booking(action="create", time="19:00"),
                    depends_on=("k1",),
                ),
            ),
            memory,
        )
        knowledge, booking = tasks
        assert knowledge.depends_on == ()
        assert booking.id == "b2" and booking.depends_on == ("k1",)
        assert booking.booking == _booking(action="create", party_size=2, time="19:00")


CHAIN = (
    Task("a", "knowledge"),
    Task("b", "booking", depends_on=("a",)),
    Task("c", "knowledge", depends_on=("b",)),
)


class TestOrdering:
    def test_dependencies_run_first(self) -> None:
        assert [t.id for t in ready_tasks(CHAIN, {}, [])] == ["a"]
        done = {"a": _result(AgentStatus.COMPLETED)}
        assert [t.id for t in ready_tasks(CHAIN, done, [])] == ["b"]

    def test_a_failed_dependency_blocks_its_dependents(self) -> None:
        failed = {"a": _result(AgentStatus.NO_ANSWER)}
        assert blocked_tasks(CHAIN, failed, []) == ["b"]
        assert blocked_tasks(CHAIN, failed, ["b"]) == ["c"]
        assert blocked_tasks(CHAIN, failed, ["b", "c"]) == []

    def test_a_cycle_is_skipped(self) -> None:
        cycle = [
            Task("x", "knowledge", depends_on=("y",)),
            Task("y", "knowledge", depends_on=("x",)),
        ]
        assert blocked_tasks(cycle, {}, []) == ["x", "y"]
