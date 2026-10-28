"""Bookings of one of the caller's business profiles (FR-04, FR-09-04; dashboard use).

Operators act on customers' behalf, so the customer-facing cancellation notice rule is not
enforced here; every change is still recorded with the operator as the actor.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.profiles import CurrentProfile
from app.auth.dependencies import CurrentAuth
from app.auth.service import AuthContext
from app.bookings.api_schemas import (
    AvailabilityOut,
    BookingEventOut,
    BookingIn,
    BookingOut,
    BookingPatch,
    CustomerOut,
    slot_out,
)
from app.bookings.availability import BookingRequest
from app.bookings.models import Booking, Customer
from app.bookings.service import (
    BookingChanges,
    BookingService,
    CustomerDetails,
    NewBooking,
)
from app.core.clock import Clock, get_clock
from app.db.session import DbSession

router = APIRouter(prefix="/profiles/{profile_id}", tags=["bookings"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or booking in your business"}
}
_CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "description": "Not available (alternatives included) or not changeable"
    }
}


def get_booking_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> BookingService:
    return BookingService(db, clock)


Service = Annotated[BookingService, Depends(get_booking_service)]


Profile = CurrentProfile


def _out(booking: Booking, customer: Customer) -> BookingOut:
    return BookingOut.model_validate(
        {**_columns(booking), "customer": CustomerOut.model_validate(customer)}
    )


def _columns(booking: Booking) -> dict[str, Any]:
    return {
        column: getattr(booking, column)
        for column in BookingOut.model_fields
        if column != "customer"
    }


def _actor(auth: AuthContext) -> str:
    return f"operator:{auth.operator_id}"


@router.get("/availability", response_model=AvailabilityOut, responses=_NOT_FOUND)
async def check_availability(
    profile: Profile,
    service: Service,
    service_id: str,
    start: datetime,
    party_size: Annotated[int, Query(ge=1, le=1000)] = 1,
    nights: Annotated[int | None, Query(ge=1, le=365)] = None,
) -> AvailabilityOut:
    result = await service.check(profile, BookingRequest(service_id, party_size, start, nights))
    return AvailabilityOut(
        available=result.check.available,
        reason=result.check.reason.value if result.check.reason else None,
        slot=slot_out(result.check),
        alternatives=[slot for alt in result.alternatives if (slot := slot_out(alt))],
    )


@router.get("/bookings", response_model=list[BookingOut], responses=_NOT_FOUND)
async def list_bookings(
    profile: Profile,
    service: Service,
    starts_from: datetime | None = None,
    starts_before: datetime | None = None,
    status_filter: Annotated[
        Literal["confirmed", "cancelled"] | None, Query(alias="status")
    ] = None,
) -> list[BookingOut]:
    rows = await service.list_bookings(
        profile, starts_from=starts_from, starts_before=starts_before, status=status_filter
    )
    return [_out(booking, customer) for booking, customer in rows]


@router.post(
    "/bookings",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def create_booking(
    profile: Profile, body: BookingIn, auth: CurrentAuth, service: Service
) -> BookingOut:
    booking = await service.create(
        profile,
        NewBooking(
            request=BookingRequest(body.service_id, body.party_size, body.start, body.nights),
            customer=CustomerDetails(body.customer.name, body.customer.phone, body.customer.email),
            details=body.details,
            notes=body.notes,
        ),
        actor=_actor(auth),
        source="dashboard",
    )
    return _out(booking, await service.customer(booking))


@router.get("/bookings/{booking_id}", response_model=BookingOut, responses=_NOT_FOUND)
async def get_booking(
    profile: Profile, booking_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> BookingOut:
    booking = await service.get(profile, booking_id)
    return _out(booking, await service.customer(booking))


@router.patch(
    "/bookings/{booking_id}", response_model=BookingOut, responses={**_NOT_FOUND, **_CONFLICT}
)
async def modify_booking(
    profile: Profile,
    booking_id: uuid.UUID,
    body: BookingPatch,
    auth: CurrentAuth,
    service: Service,
) -> BookingOut:
    booking = await service.get(profile, booking_id)
    changes = BookingChanges(**body.model_dump(exclude_unset=True))
    booking = await service.modify(
        profile, booking, changes, actor=_actor(auth), enforce_policy=False
    )
    return _out(booking, await service.customer(booking))


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=BookingOut,
    responses={**_NOT_FOUND, **_CONFLICT},
)
async def cancel_booking(
    profile: Profile, booking_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> BookingOut:
    booking = await service.get(profile, booking_id)
    booking = await service.cancel(profile, booking, actor=_actor(auth), enforce_policy=False)
    return _out(booking, await service.customer(booking))


@router.get(
    "/bookings/{booking_id}/events", response_model=list[BookingEventOut], responses=_NOT_FOUND
)
async def booking_history(
    profile: Profile, booking_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> list[Any]:
    booking = await service.get(profile, booking_id)
    return await service.events(booking)
