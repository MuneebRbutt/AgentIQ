"""The Booking agent (FR-04-01…06, UC-04).

Takes a structured booking subtask, works out what is still missing, checks availability,
asks for confirmation before committing (FR-04-03), and creates, changes or cancels the
booking through `BookingService`, which enforces every rule. Wording comes only from the
profile (service and resource names, policies), so the same code serves every domain.
"""

import datetime as dt
from enum import StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agents.contracts import AgentContext, AgentResult, AgentStatus
from app.bookings.availability import BookingRequest, Check, Unavailable
from app.bookings.models import Booking
from app.bookings.service import (
    BookingChanges,
    BookingService,
    BookingUnavailableError,
    ChangeWindowClosedError,
    CustomerDetails,
    NewBooking,
    missing_details,
    profile_config,
)
from app.core.errors import NotFoundError
from app.profiles.schema import BookingMode, BusinessProfileConfig

AGENT_NAME = "booking"
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]


class BookingAction(StrEnum):
    CHECK = "check"
    CREATE = "create"
    MODIFY = "modify"
    CANCEL = "cancel"


class BookingSubtask(BaseModel):
    """Details the orchestrator extracted from the conversation so far."""

    model_config = ConfigDict(extra="forbid")

    action: BookingAction
    service_id: str | None = None
    party_size: int | None = Field(default=None, ge=1, le=1000)
    date: dt.date | None = None  # local to the business
    time: dt.time | None = None  # time-slot bookings only
    nights: int | None = Field(default=None, ge=1, le=365)  # overnight bookings only
    customer_name: Text | None = None
    customer_phone: Text | None = None
    customer_email: Text | None = None
    details: dict[str, Text] = Field(default_factory=dict)  # the profile's custom fields
    notes: Annotated[str, StringConstraints(max_length=1000)] | None = None
    reference: Text | None = None  # which booking to change or cancel
    confirmed: bool = False  # the customer agreed to the read-back details


_REASONS = {
    Unavailable.UNKNOWN_SERVICE: "That service isn't offered.",
    Unavailable.PARTY_SIZE: "No option fits that number of people.",
    Unavailable.NIGHTS: "That length of stay isn't allowed.",
    Unavailable.TOO_SOON: "That's too soon to book.",
    Unavailable.TOO_FAR_AHEAD: "That's too far ahead to book.",
    Unavailable.NOT_ON_SLOT: "Bookings start at set times.",
    Unavailable.CLOSED: "The business is closed at that time.",
    Unavailable.NO_CAPACITY: "That time is fully booked.",
}


class BookingAgent:
    def __init__(self, bookings: BookingService) -> None:
        self._bookings = bookings

    async def run(self, context: AgentContext, task: BookingSubtask) -> AgentResult:
        config = profile_config(context.profile)
        if task.customer_phone is None and context.customer_phone:
            task = task.model_copy(update={"customer_phone": context.customer_phone})
        if task.service_id is None:
            task = task.model_copy(update={"service_id": _only_service(config)})

        if task.action in {BookingAction.CHECK, BookingAction.CREATE}:
            return await self._check_or_create(context, config, task)
        return await self._change(context, config, task)

    # -- new bookings ----------------------------------------------------------------------

    async def _check_or_create(
        self, context: AgentContext, config: BusinessProfileConfig, task: BookingSubtask
    ) -> AgentResult:
        missing = _missing_request_fields(config, task)
        if task.action is BookingAction.CREATE:
            missing += _missing_customer_fields(config, task)
        if missing:
            return _result(
                AgentStatus.NEEDS_INFO, "Some details are needed first.", missing=missing
            )

        request = _request(config, task)
        availability = await self._bookings.check(context.profile, request)
        if not availability.check.available:
            return _unavailable(config, availability.check, availability.alternatives)
        summary = _describe(config, availability.check)

        if task.action is BookingAction.CHECK:
            return _result(
                AgentStatus.COMPLETED, f"Available: {summary}", data=_slot(availability.check)
            )
        if not task.confirmed:
            return _result(
                AgentStatus.NEEDS_CONFIRMATION,
                f"Please confirm: {summary}",
                data=_slot(availability.check),
            )

        try:
            booking = await self._bookings.create(
                context.profile,
                NewBooking(
                    request=request,
                    customer=_customer(task),
                    details=task.details,
                    notes=task.notes,
                ),
                actor="customer",
                source="agent",
            )
        except BookingUnavailableError as exc:  # taken while the customer was confirming
            return _unavailable(config, exc.check, exc.alternatives)
        return _result(
            AgentStatus.COMPLETED,
            f"Booked: {summary} Your reference is {booking.reference}.",
            data=_booking_data(booking),
        )

    # -- existing bookings -----------------------------------------------------------------

    async def _change(
        self, context: AgentContext, config: BusinessProfileConfig, task: BookingSubtask
    ) -> AgentResult:
        missing = [name for name in ("reference", "customer_phone") if not getattr(task, name)]
        if missing:
            return _result(
                AgentStatus.NEEDS_INFO,
                "The booking reference and phone number are needed.",
                missing=missing,
            )
        assert task.reference and task.customer_phone  # noqa: S101  # narrowed just above
        try:
            booking = await self._bookings.find_for_customer(
                context.profile, task.reference, task.customer_phone
            )
        except NotFoundError:
            return _result(
                AgentStatus.REJECTED,
                "No booking matches that reference and phone number.",
            )
        if task.action is BookingAction.CANCEL:
            return await self._cancel(context, config, booking, task)
        return await self._modify(context, config, booking, task)

    async def _cancel(
        self,
        context: AgentContext,
        config: BusinessProfileConfig,
        booking: Booking,
        task: BookingSubtask,
    ) -> AgentResult:
        summary = _describe_booking(config, booking)
        if not task.confirmed:
            return _result(
                AgentStatus.NEEDS_CONFIRMATION,
                f"Please confirm cancelling: {summary}",
                data=_booking_data(booking),
            )
        try:
            booking = await self._bookings.cancel(
                context.profile, booking, actor="customer", enforce_policy=True
            )
        except ChangeWindowClosedError as exc:
            return _rejected_by_policy(config, exc)
        return _result(AgentStatus.COMPLETED, f"Cancelled: {summary}", data=_booking_data(booking))

    async def _modify(
        self,
        context: AgentContext,
        config: BusinessProfileConfig,
        booking: Booking,
        task: BookingSubtask,
    ) -> AgentResult:
        changes = _changes(config, booking, task)
        if changes == BookingChanges():
            return _result(AgentStatus.NEEDS_INFO, "What should be changed?", missing=["changes"])

        if not task.confirmed:
            request = _changed_request(config, booking, changes)
            availability = await self._bookings.check(
                context.profile, request, ignore_booking=booking.id
            )
            if not availability.check.available:
                return _unavailable(config, availability.check, availability.alternatives)
            return _result(
                AgentStatus.NEEDS_CONFIRMATION,
                f"Please confirm the change to: {_describe(config, availability.check)}",
                data=_slot(availability.check),
            )
        try:
            booking = await self._bookings.modify(
                context.profile, booking, changes, actor="customer", enforce_policy=True
            )
        except ChangeWindowClosedError as exc:
            return _rejected_by_policy(config, exc)
        except BookingUnavailableError as exc:
            return _unavailable(config, exc.check, exc.alternatives)
        return _result(
            AgentStatus.COMPLETED,
            f"Changed: {_describe_booking(config, booking)}",
            data=_booking_data(booking),
        )


# -- helpers -------------------------------------------------------------------------------


def _result(status: AgentStatus, message: str, **fields: Any) -> AgentResult:
    return AgentResult(agent=AGENT_NAME, status=status, message=message, **fields)


def _only_service(config: BusinessProfileConfig) -> str | None:
    """The service to assume when the customer didn't say: only if there is exactly one."""
    bookable = {
        service.id
        for service in config.services
        for resource in config.resources
        if not resource.service_ids or service.id in resource.service_ids
    }
    return bookable.pop() if len(bookable) == 1 else None


def _missing_request_fields(config: BusinessProfileConfig, task: BookingSubtask) -> list[str]:
    rules = config.booking_rules
    missing = [name for name in ("service_id", "date") if getattr(task, name) is None]
    if rules.mode is BookingMode.TIME_SLOT and task.time is None:
        missing.append("time")
    if rules.mode is BookingMode.OVERNIGHT and task.nights is None:
        missing.append("nights")
    # Don't ask how many people when only one is ever allowed.
    if task.party_size is None and rules.max_party_size > 1:
        missing.append("party_size")
    return missing


def _missing_customer_fields(config: BusinessProfileConfig, task: BookingSubtask) -> list[str]:
    names = {"name": "customer_name", "phone": "customer_phone", "email": "customer_email"}
    missing = missing_details(config, _customer(task), task.details)
    return [names.get(key, f"details.{key}") for key in missing]


def _customer(task: BookingSubtask) -> CustomerDetails:
    return CustomerDetails(
        name=task.customer_name or "", phone=task.customer_phone or "", email=task.customer_email
    )


def _request(config: BusinessProfileConfig, task: BookingSubtask) -> BookingRequest:
    assert task.service_id and task.date  # noqa: S101  # checked by _missing_request_fields
    start_time = task.time or dt.time()
    party = task.party_size or config.booking_rules.min_party_size
    return BookingRequest(
        task.service_id, party, dt.datetime.combine(task.date, start_time), task.nights
    )


def _changes(
    config: BusinessProfileConfig, booking: Booking, task: BookingSubtask
) -> BookingChanges:
    start = None
    if task.date or task.time:
        current = _local(config, booking.starts_at)
        start = dt.datetime.combine(task.date or current.date(), task.time or current.time())
    return BookingChanges(
        start=start,
        party_size=task.party_size,
        service_id=task.service_id if task.service_id != booking.service_id else None,
        nights=task.nights,
        details=task.details or None,
        notes=task.notes,
    )


def _changed_request(
    config: BusinessProfileConfig, booking: Booking, changes: BookingChanges
) -> BookingRequest:
    current = _local(config, booking.starts_at)
    nights = (_local(config, booking.ends_at).date() - current.date()).days or None
    return BookingRequest(
        changes.service_id or booking.service_id,
        changes.party_size or booking.party_size,
        changes.start or current,
        changes.nights or nights,
    )


def _unavailable(
    config: BusinessProfileConfig, check: Check, alternatives: list[Check]
) -> AgentResult:
    reason = _REASONS[check.reason] if check.reason else "That isn't available."
    offered = [_describe(config, alt) for alt in alternatives]
    message = reason + (" Nearest options: " + "; ".join(offered) if offered else "")
    return _result(
        AgentStatus.UNAVAILABLE,
        message,
        data={"reason": check.reason.value if check.reason else None},
        alternatives=[
            {**_slot(alt), "description": text}
            for alt, text in zip(alternatives, offered, strict=True)
        ],
    )


def _rejected_by_policy(
    config: BusinessProfileConfig, error: ChangeWindowClosedError
) -> AgentResult:
    # Quote the business's own cancellation policy when it has one (UC-04 exception path).
    policy = next((p.text for p in config.policies if "cancel" in p.title.lower()), None)
    return _result(
        AgentStatus.REJECTED,
        error.detail + (f" Policy: {policy}" if policy else ""),
        data={"cancellation_notice_hours": error.notice_hours},
    )


def _local(config: BusinessProfileConfig, moment: dt.datetime) -> dt.datetime:
    return moment.astimezone(ZoneInfo(config.timezone)).replace(tzinfo=None)


def _when(config: BusinessProfileConfig, start: dt.datetime, end: dt.datetime) -> str:
    local_start, local_end = _local(config, start), _local(config, end)
    day = f"{local_start:%A} {local_start.day} {local_start:%B}"
    if config.booking_rules.mode is BookingMode.OVERNIGHT:
        nights = (local_end.date() - local_start.date()).days
        return (
            f"{nights} night{'s' if nights != 1 else ''} from {day} (check-in {local_start:%H:%M})"
        )
    return f"{day} at {local_start:%H:%M}"


def _names(config: BusinessProfileConfig, service_id: str, resource_id: str) -> tuple[str, str]:
    service = next((s.name for s in config.services if s.id == service_id), service_id)
    resource = next((r.name for r in config.resources if r.id == resource_id), resource_id)
    return service, resource


def _describe(config: BusinessProfileConfig, check: Check) -> str:
    period, resource_id = check.slot()
    service, resource = _names(config, check.request.service_id, resource_id)
    people = check.request.party_size
    return (
        f"{service} ({resource}) for {people} {'person' if people == 1 else 'people'}, "
        f"{_when(config, period.starts_at, period.ends_at)}."
    )


def _describe_booking(config: BusinessProfileConfig, booking: Booking) -> str:
    service, resource = _names(config, booking.service_id, booking.resource_id)
    people = booking.party_size
    return (
        f"{service} ({resource}) for {people} {'person' if people == 1 else 'people'}, "
        f"{_when(config, booking.starts_at, booking.ends_at)}, reference {booking.reference}."
    )


def _slot(check: Check) -> dict[str, Any]:
    period, resource_id = check.slot()
    return {
        "service_id": check.request.service_id,
        "resource_id": resource_id,
        "party_size": check.request.party_size,
        "starts_at": period.starts_at.isoformat(),
        "ends_at": period.ends_at.isoformat(),
    }


def _booking_data(booking: Booking) -> dict[str, Any]:
    return {
        "booking_id": str(booking.id),
        "reference": booking.reference,
        "status": booking.status,
        "service_id": booking.service_id,
        "resource_id": booking.resource_id,
        "party_size": booking.party_size,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
    }
