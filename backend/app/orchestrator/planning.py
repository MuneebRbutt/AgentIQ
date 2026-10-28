"""Turning an understanding into the turn's tasks, and running order (FR-03-02/03).

Tasks without dependencies run in parallel. A task that depends on others runs after
them, and only if they all completed ("if you have a room, book it"); otherwise it is
skipped and the reply explains what happened instead.
"""

from collections.abc import Mapping, Sequence

from app.agents.contracts import AgentResult, AgentStatus
from app.orchestrator.memory import SessionMemory, merge_booking
from app.orchestrator.understanding import Parsed, Task

MAX_KNOWLEDGE_TASKS = 3
NOTE_DECLINED = "declined_confirmation"


def plan(parsed: Parsed, memory: SessionMemory) -> tuple[list[Task], list[str]]:
    """The tasks to run this turn, and notes for the reply. Updates `memory`'s draft."""
    notes: list[str] = []
    knowledge = [t for t in parsed.tasks if t.agent == "knowledge"][:MAX_KNOWLEDGE_TASKS]
    bookings = [t for t in parsed.tasks if t.agent == "booking"]
    tasks: list[Task] = list(knowledge)

    draft = memory.draft()
    confirmation = parsed.understanding.confirmation
    if bookings:
        # Several booking subtasks in one turn describe one booking; fold them together.
        for update in bookings:
            if update.booking is not None:
                draft = merge_booking(draft, update.booking)
        depends = tuple(dict.fromkeys(d for t in bookings for d in t.depends_on))
        if draft is not None:
            memory.booking = draft.model_dump(mode="json", exclude={"confirmed"})
            tasks.append(Task(bookings[-1].id, "booking", booking=draft, depends_on=depends))
    elif memory.awaiting_confirmation and draft is not None and confirmation == "yes":
        # The customer agreed to exactly what was read back: now it may be committed.
        confirmed = draft.model_copy(update={"confirmed": True})
        tasks.append(Task("confirm", "booking", booking=confirmed))
    elif memory.awaiting_confirmation and confirmation == "no":
        notes.append(NOTE_DECLINED)

    memory.awaiting_confirmation = False
    known = {task.id for task in tasks}
    return [_known_dependencies(task, known) for task in tasks], notes


def _known_dependencies(task: Task, known: set[str]) -> Task:
    depends = tuple(d for d in task.depends_on if d in known and d != task.id)
    if depends == task.depends_on:
        return task
    return Task(task.id, task.agent, task.knowledge, task.booking, depends)


def ready_tasks(
    tasks: Sequence[Task], results: Mapping[str, AgentResult], skipped: Sequence[str]
) -> list[Task]:
    """Tasks not yet run whose dependencies have all completed."""
    return [
        task
        for task in tasks
        if task.id not in results
        and task.id not in skipped
        and all(_completed(results.get(d)) for d in task.depends_on)
    ]


def blocked_tasks(
    tasks: Sequence[Task], results: Mapping[str, AgentResult], skipped: Sequence[str]
) -> list[str]:
    """Tasks that can no longer run: a dependency didn't complete, or they wait in a cycle."""
    pending = [t for t in tasks if t.id not in results and t.id not in skipped]
    failed = [
        t.id
        for t in pending
        if any(d in skipped or (d in results and not _completed(results[d])) for d in t.depends_on)
    ]
    if failed:
        return failed
    if pending and not ready_tasks(tasks, results, skipped):
        return [t.id for t in pending]  # nothing can make progress: a dependency cycle
    return []


def _completed(result: AgentResult | None) -> bool:
    return result is not None and result.status is AgentStatus.COMPLETED
