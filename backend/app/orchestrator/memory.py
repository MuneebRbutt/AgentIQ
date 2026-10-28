"""What the orchestrator remembers between turns, and the rules for updating it.

A booking is usually put together over several turns ("a table for four" ... "tomorrow at
eight" ... "Ali Raza" ... "yes"). The draft lives here, in the interaction row, and each
turn's new details are merged into it. Whether the customer has actually confirmed is
decided by these rules, not by the model: a booking is only committed when the previous
reply read it back and the customer said yes without changing anything (FR-04-03).
"""

from typing import Any

from pydantic import BaseModel

from app.agents.booking.agent import BookingAction, BookingSubtask
from app.agents.contracts import AgentResult, AgentStatus

_NEW = {BookingAction.CHECK, BookingAction.CREATE}
_FINAL = {BookingAction.CREATE, BookingAction.MODIFY, BookingAction.CANCEL}


class SessionMemory(BaseModel):
    booking: dict[str, Any] | None = None  # BookingSubtask fields, never `confirmed`
    awaiting_confirmation: bool = False
    clarifications: int = 0  # unclear turns in a row
    negative_streak: int = 0  # upset customer messages in a row (FR-06-02)
    handed_off: bool = False

    def draft(self) -> BookingSubtask | None:
        return BookingSubtask.model_validate(self.booking) if self.booking else None


def merge_booking(draft: BookingSubtask | None, update: BookingSubtask) -> BookingSubtask:
    """The draft with this turn's details added. A switch between making a new booking and
    changing an existing one starts over; "check" followed by "book it" keeps the details."""
    if draft is None or (draft.action in _NEW) != (update.action in _NEW):
        return update.model_copy(update={"confirmed": False})
    given = update.model_dump(exclude_unset=True, exclude={"confirmed"})
    merged = {**draft.model_dump(exclude={"confirmed"}), **given}
    merged["details"] = {**draft.details, **update.details}
    if draft.action is BookingAction.CREATE and update.action is BookingAction.CHECK:
        merged["action"] = BookingAction.CREATE  # asking about another time isn't cancelling
    return BookingSubtask.model_validate({**merged, "confirmed": False})


def remember_result(memory: SessionMemory, task: BookingSubtask, result: AgentResult) -> None:
    """Update the draft from what the Booking agent said about it."""
    memory.awaiting_confirmation = result.status is AgentStatus.NEEDS_CONFIRMATION
    if result.status is AgentStatus.COMPLETED and task.action in _FINAL:
        memory.booking = None  # done; the next booking starts fresh
    else:
        memory.booking = task.model_dump(mode="json", exclude={"confirmed"})
