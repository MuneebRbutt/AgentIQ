"""Create, change and cancel bookings within one business profile (FR-04).

Used by both the Booking agent (customers) and the dashboard API (operators). Every
change re-checks availability in the database (FR-04-02) while holding a per-profile lock,
so two simultaneous requests can't both take the last unit, and every change is recorded
as a `BookingEvent` (FR-04-06).
"""

import re
import secrets
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.availability import (
    ALTERNATIVE_SEARCH_DAYS,
    AvailabilityChecker,
    BookingRequest,
    Check,
    Period,
    Unavailable,
)
from app.bookings.models import Booking, BookingEvent, Customer
from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.profiles.models import BusinessProfile
from app.profiles.schema import BusinessProfileConfig

log = get_logger(__name__)

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
BOOKING_NOT_FOUND = "Booking not found."
# Unambiguous characters only (no 0/O, 1/I/L), so references survive being read aloud.
_REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_REFERENCE_LENGTH = 6


@dataclass(frozen=True, slots=True)
class CustomerDetails:
    name: str
    phone: str
    email: str | None = None


@dataclass(frozen=True, slots=True)
class NewBooking:
    request: BookingRequest
    customer: CustomerDetails
    details: dict[str, str] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class BookingChanges:
    """Attributes a customer may change (FR-04-04). `None` keeps the current value."""

    start: datetime | None = None
    party_size: int | None = None
    service_id: str | None = None
    nights: int | None = None
    details: dict[str, str] | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    check: Check
    alternatives: list[Check]


class BookingUnavailableError(ConflictError):
    code = "unavailable"

    def __init__(self, check: Check, alternatives: list[Check]) -> None:
        super().__init__(
            "That time isn't available.",
            extra={
                "reason": check.reason.value if check.reason else None,
                "alternatives": [_check_json(alt) for alt in alternatives],
            },
        )
        self.check = check
        self.alternatives = alternatives


class ChangeWindowClosedError(ConflictError):
    """A customer tried to change or cancel too close to the booking (FR-04-05)."""

    code = "change_window_closed"

    def __init__(self, notice_hours: int) -> None:
        super().__init__(
            f"Bookings can only be changed or cancelled up to {notice_hours} hours before "
            "they start.",
            extra={"cancellation_notice_hours": notice_hours},
        )
        self.notice_hours = notice_hours


class MissingDetailsError(ValidationError):
    code = "missing_details"

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "Some booking details are missing: " + ", ".join(missing) + ".",
            extra={"missing": missing},
        )
        self.missing = missing


def phone_key(phone: str) -> str:
    """Last 10 digits: matches local and international spellings of one number."""
    return re.sub(r"\D", "", phone)[-10:]


def profile_config(profile: BusinessProfile) -> BusinessProfileConfig:
    return BusinessProfileConfig.model_validate(profile.config)


def to_local(moment: datetime, config: BusinessProfileConfig) -> datetime:
    """A naive wall-clock time at the business (aware inputs are converted first)."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(ZoneInfo(config.timezone)).replace(tzinfo=None)


def missing_details(
    config: BusinessProfileConfig, customer: CustomerDetails, details: dict[str, str]
) -> list[str]:
    """What the profile requires that hasn't been given (FR-04-01).

    Name and phone are always required: the phone number identifies the customer (AS-5).
    """
    missing = [key for key in ("name", "phone") if not getattr(customer, key).strip()]
    if "email" in config.booking_rules.required_details and not customer.email:
        missing.append("email")
    missing += [
        custom.key
        for custom in config.booking_rules.custom_fields
        if custom.required and not details.get(custom.key, "").strip()
    ]
    return missing


class BookingService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    # -- reading -------------------------------------------------------------------------

    async def check(
        self,
        profile: BusinessProfile,
        request: BookingRequest,
        *,
        ignore_booking: uuid.UUID | None = None,
    ) -> AvailabilityResult:
        config = profile_config(profile)
        request = replace(request, start=to_local(request.start, config))
        checker = await self._checker(profile, config, request, ignore_booking)
        check = checker.check(request)
        alternatives = [] if check.available else _alternatives_for(checker, check)
        return AvailabilityResult(check, alternatives)

    async def get(self, profile: BusinessProfile, booking_id: uuid.UUID) -> Booking:
        booking = await self._db.scalar(
            select(Booking).where(
                Booking.tenant_id == profile.tenant_id,
                Booking.profile_id == profile.id,
                Booking.id == booking_id,
            )
        )
        if booking is None:
            raise NotFoundError(BOOKING_NOT_FOUND)
        return booking

    async def find_for_customer(
        self, profile: BusinessProfile, reference: str, phone: str
    ) -> Booking:
        """A customer's booking by reference, only if the phone number matches (AS-5)."""
        row = await self._db.execute(
            select(Booking, Customer.phone_key)
            .join(Customer, Booking.customer_id == Customer.id)
            .where(
                Booking.tenant_id == profile.tenant_id,
                Booking.profile_id == profile.id,
                Booking.reference == reference.strip().upper(),
            )
        )
        found = row.one_or_none()
        if found is None or found[1] != phone_key(phone):
            raise NotFoundError(BOOKING_NOT_FOUND)
        booking: Booking = found[0]
        return booking

    async def list_bookings(
        self,
        profile: BusinessProfile,
        *,
        starts_from: datetime | None = None,
        starts_before: datetime | None = None,
        status: str | None = None,
    ) -> Sequence[tuple[Booking, Customer]]:
        statement = (
            select(Booking, Customer)
            .join(Customer, Booking.customer_id == Customer.id)
            .where(Booking.tenant_id == profile.tenant_id, Booking.profile_id == profile.id)
            .order_by(Booking.starts_at)
        )
        if starts_from is not None:
            statement = statement.where(Booking.starts_at >= starts_from)
        if starts_before is not None:
            statement = statement.where(Booking.starts_at < starts_before)
        if status is not None:
            statement = statement.where(Booking.status == status)
        return [(booking, customer) for booking, customer in await self._db.execute(statement)]

    async def customer(self, booking: Booking) -> Customer:
        return await self._db.get_one(Customer, booking.customer_id)

    async def events(self, booking: Booking) -> list[BookingEvent]:
        statement = (
            select(BookingEvent)
            .where(BookingEvent.booking_id == booking.id)
            .order_by(BookingEvent.seq)
        )
        return list(await self._db.scalars(statement))

    # -- changing ------------------------------------------------------------------------

    async def create(
        self, profile: BusinessProfile, new: NewBooking, *, actor: str, source: str
    ) -> Booking:
        config = profile_config(profile)
        missing = missing_details(config, new.customer, new.details)
        if missing:
            raise MissingDetailsError(missing)
        request = replace(new.request, start=to_local(new.request.start, config))

        await self._lock(profile)
        checker = await self._checker(profile, config, request, None)
        check = checker.check(request)
        if not check.available:
            await self._db.rollback()
            raise BookingUnavailableError(check, _alternatives_for(checker, check))
        period, resource_id = check.slot()

        now = self._clock.now()
        customer = await self._upsert_customer(profile, new.customer)
        booking = Booking(
            id=uuid.uuid4(),
            tenant_id=profile.tenant_id,
            profile_id=profile.id,
            customer_id=customer.id,
            reference=await self._new_reference(profile),
            service_id=request.service_id,
            resource_id=resource_id,
            party_size=request.party_size,
            starts_at=period.starts_at.astimezone(UTC),
            ends_at=period.ends_at.astimezone(UTC),
            status=CONFIRMED,
            details=dict(new.details),
            notes=new.notes,
            source=source,
            created_at=now,
            updated_at=now,
        )
        self._db.add(booking)
        self._record(booking, "created", actor, _snapshot(booking))
        await self._db.commit()
        log.info("booking_created", booking_id=str(booking.id), profile_id=str(profile.id))
        return booking

    async def modify(
        self,
        profile: BusinessProfile,
        booking: Booking,
        changes: BookingChanges,
        *,
        actor: str,
        enforce_policy: bool,
    ) -> Booking:
        config = profile_config(profile)
        self._require_changeable(booking, config, enforce_policy=enforce_policy)
        zone = ZoneInfo(config.timezone)
        current_start = booking.starts_at.astimezone(zone).replace(tzinfo=None)
        request = BookingRequest(
            service_id=changes.service_id or booking.service_id,
            party_size=changes.party_size or booking.party_size,
            start=to_local(changes.start, config) if changes.start else current_start,
            nights=changes.nights or _nights(booking, zone),
        )
        details = {**booking.details, **(changes.details or {})}
        customer = await self.customer(booking)
        missing = missing_details(
            config, CustomerDetails(customer.name, customer.phone, customer.email), details
        )
        if missing:
            raise MissingDetailsError(missing)

        await self._lock(profile)
        checker = await self._checker(profile, config, request, booking.id)
        check = checker.check(request)
        if not check.available:
            await self._db.rollback()
            raise BookingUnavailableError(check, _alternatives_for(checker, check))
        period, resource_id = check.slot()

        before = _snapshot(booking)
        booking.service_id = request.service_id
        booking.resource_id = resource_id
        booking.party_size = request.party_size
        booking.starts_at = period.starts_at.astimezone(UTC)
        booking.ends_at = period.ends_at.astimezone(UTC)
        booking.details = details
        if changes.notes is not None:
            booking.notes = changes.notes
        booking.updated_at = self._clock.now()
        after = _snapshot(booking)
        diff = {
            key: {"from": before[key], "to": after[key]}
            for key in after
            if before[key] != after[key]
        }
        self._record(booking, "modified", actor, diff)
        await self._db.commit()
        log.info("booking_modified", booking_id=str(booking.id), fields=sorted(diff))
        return booking

    async def cancel(
        self, profile: BusinessProfile, booking: Booking, *, actor: str, enforce_policy: bool
    ) -> Booking:
        config = profile_config(profile)
        self._require_changeable(booking, config, enforce_policy=enforce_policy)
        now = self._clock.now()
        booking.status = CANCELLED
        booking.cancelled_at = now
        booking.updated_at = now
        self._record(booking, "cancelled", actor, {"status": {"from": CONFIRMED, "to": CANCELLED}})
        await self._db.commit()
        log.info("booking_cancelled", booking_id=str(booking.id))
        return booking

    # -- helpers -------------------------------------------------------------------------

    def _require_changeable(
        self, booking: Booking, config: BusinessProfileConfig, *, enforce_policy: bool
    ) -> None:
        if booking.status != CONFIRMED:
            raise ConflictError("This booking is already cancelled.")
        if booking.starts_at <= self._clock.now():
            raise ConflictError("This booking has already started.")
        notice = config.booking_rules.cancellation_notice_hours
        # Operators may override the customer-facing rule from the dashboard.
        if enforce_policy and self._clock.now() > booking.starts_at - timedelta(hours=notice):
            raise ChangeWindowClosedError(notice)

    async def _lock(self, profile: BusinessProfile) -> None:
        """Serialize bookings for one profile until the transaction ends."""
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"bookings:{profile.id}"},
        )

    async def _checker(
        self,
        profile: BusinessProfile,
        config: BusinessProfileConfig,
        request: BookingRequest,
        ignore_booking: uuid.UUID | None,
    ) -> AvailabilityChecker:
        # Load every booking that could overlap the request or its alternatives.
        zone = ZoneInfo(config.timezone)
        local_start = request.start.replace(tzinfo=zone)
        margin = timedelta(days=ALTERNATIVE_SEARCH_DAYS + 2)
        longest = timedelta(days=config.booking_rules.max_nights or 1, hours=24)
        statement = select(Booking.resource_id, Booking.starts_at, Booking.ends_at).where(
            Booking.tenant_id == profile.tenant_id,
            Booking.profile_id == profile.id,
            Booking.status == CONFIRMED,
            Booking.starts_at < local_start + margin + longest,
            Booking.ends_at > local_start - margin,
        )
        if ignore_booking is not None:
            statement = statement.where(Booking.id != ignore_booking)
        taken: defaultdict[str, list[Period]] = defaultdict(list)
        for resource_id, starts_at, ends_at in await self._db.execute(statement):
            taken[resource_id].append(Period(starts_at, ends_at))
        return AvailabilityChecker(config, taken, self._clock.now())

    async def _upsert_customer(
        self, profile: BusinessProfile, details: CustomerDetails
    ) -> Customer:
        key = phone_key(details.phone)
        customer = await self._db.scalar(
            select(Customer).where(
                Customer.tenant_id == profile.tenant_id, Customer.phone_key == key
            )
        )
        if customer is None:
            customer = Customer(
                id=uuid.uuid4(),
                tenant_id=profile.tenant_id,
                name=details.name.strip(),
                phone=details.phone.strip(),
                phone_key=key,
                email=details.email,
            )
            self._db.add(customer)
        else:
            customer.name = details.name.strip()
            customer.email = details.email or customer.email
        return customer

    async def _new_reference(self, profile: BusinessProfile) -> str:
        while True:
            reference = "".join(
                secrets.choice(_REFERENCE_ALPHABET) for _ in range(_REFERENCE_LENGTH)
            )
            taken = await self._db.scalar(
                select(func.count())
                .select_from(Booking)
                .where(Booking.tenant_id == profile.tenant_id, Booking.reference == reference)
            )
            if not taken:
                return reference

    def _record(self, booking: Booking, kind: str, actor: str, changes: dict[str, Any]) -> None:
        self._db.add(
            BookingEvent(
                id=uuid.uuid4(),
                booking_id=booking.id,
                kind=kind,
                actor=actor,
                changes=changes,
                created_at=self._clock.now(),
            )
        )


def _alternatives_for(checker: AvailabilityChecker, check: Check) -> list[Check]:
    # Only worth searching when the request itself was acceptable apart from timing.
    if check.reason in {Unavailable.UNKNOWN_SERVICE, Unavailable.PARTY_SIZE, Unavailable.NIGHTS}:
        return []
    return checker.alternatives(check.request)


def _nights(booking: Booking, zone: ZoneInfo) -> int:
    start = booking.starts_at.astimezone(zone).date()
    end = booking.ends_at.astimezone(zone).date()
    return max((end - start).days, 1)


def _snapshot(booking: Booking) -> dict[str, Any]:
    return {
        "service_id": booking.service_id,
        "resource_id": booking.resource_id,
        "party_size": booking.party_size,
        "starts_at": booking.starts_at.isoformat(),
        "ends_at": booking.ends_at.isoformat(),
        "details": dict(booking.details),
        "notes": booking.notes,
    }


def _check_json(check: Check) -> dict[str, Any]:
    period, resource_id = check.slot()
    return {
        "resource_id": resource_id,
        "starts_at": period.starts_at.isoformat(),
        "ends_at": period.ends_at.isoformat(),
    }
