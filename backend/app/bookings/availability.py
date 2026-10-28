"""Availability rules (FR-04-02), as pure functions of a profile, existing bookings and now.

Nothing here knows about hotels, restaurants or clinics (NFR05-02). A request is checked
against the profile's booking rules and opening hours, then placed on the smallest
suitable resource that still has a free unit for the whole period (ADR-0003).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.profiles.schema import BookingMode, BusinessProfileConfig, Resource, Service, Weekday

ALTERNATIVE_SEARCH_DAYS = 7


class Unavailable(StrEnum):
    UNKNOWN_SERVICE = "unknown_service"
    PARTY_SIZE = "party_size"
    NIGHTS = "nights"
    TOO_SOON = "too_soon"
    TOO_FAR_AHEAD = "too_far_ahead"
    NOT_ON_SLOT = "not_on_slot"
    CLOSED = "closed"
    NO_CAPACITY = "no_capacity"


@dataclass(frozen=True, slots=True)
class BookingRequest:
    service_id: str
    party_size: int
    # Local wall-clock time at the business. For overnight bookings only the date counts.
    start: datetime
    nights: int | None = None


@dataclass(frozen=True, slots=True)
class Period:
    starts_at: datetime  # timezone-aware
    ends_at: datetime

    def overlaps(self, other: "Period") -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


@dataclass(frozen=True, slots=True)
class Check:
    request: BookingRequest
    reason: Unavailable | None = None
    period: Period | None = None
    resource_id: str | None = None

    @property
    def available(self) -> bool:
        return self.reason is None

    def slot(self) -> tuple[Period, str]:
        """The period and resource of an available check."""
        if self.period is None or self.resource_id is None:
            raise ValueError(f"not available: {self.reason}")
        return self.period, self.resource_id


class AvailabilityChecker:
    """Answers "can this be booked?" for one profile against a snapshot of its bookings.

    `taken` maps each resource id to the periods already booked on it. It should cover
    every booking that might overlap the requests being checked.
    """

    def __init__(
        self,
        profile: BusinessProfileConfig,
        taken: Mapping[str, Sequence[Period]],
        now: datetime,
    ) -> None:
        self._profile = profile
        self._rules = profile.booking_rules
        self._zone = ZoneInfo(profile.timezone)
        self._taken = taken
        self._now = now

    def check(self, request: BookingRequest) -> Check:
        service = next((s for s in self._profile.services if s.id == request.service_id), None)
        if service is None:
            return Check(request, Unavailable.UNKNOWN_SERVICE)
        if not self._rules.min_party_size <= request.party_size <= self._rules.max_party_size:
            return Check(request, Unavailable.PARTY_SIZE)

        period_or_reason = self.period_for(request, service)
        if isinstance(period_or_reason, Unavailable):
            return Check(request, period_or_reason)
        period = period_or_reason

        candidates = self._candidates(service, request.party_size)
        if not candidates:
            return Check(request, Unavailable.PARTY_SIZE, period)
        for resource in candidates:
            if self._free_units(resource, period) > 0:
                return Check(request, None, period, resource.id)
        return Check(request, Unavailable.NO_CAPACITY, period)

    def period_for(self, request: BookingRequest, service: Service) -> Period | Unavailable:
        """The period a request would occupy, or why the rules don't allow it."""
        if self._rules.mode is BookingMode.OVERNIGHT:
            nights = request.nights or 1
            if nights < 1 or (self._rules.max_nights and nights > self._rules.max_nights):
                return Unavailable.NIGHTS
            check_in = _configured(self._rules.check_in_time, "check_in_time")
            check_out = _configured(self._rules.check_out_time, "check_out_time")
            day = request.start.date()
            start = datetime.combine(day, check_in)
            end = datetime.combine(day + timedelta(days=nights), check_out)
        else:
            start = request.start.replace(second=0, microsecond=0)
            if request.start != start or self._minute_of_day(start) % self._slot_minutes:
                return Unavailable.NOT_ON_SLOT
            end = start + timedelta(minutes=self._duration_minutes(service))
            if not self._within_opening_hours(start, end):
                return Unavailable.CLOSED

        period = Period(self._aware(start), self._aware(end))
        if period.starts_at < self._now + timedelta(minutes=self._rules.min_notice_minutes):
            return Unavailable.TOO_SOON
        if period.starts_at > self._now + timedelta(days=self._rules.max_advance_days):
            return Unavailable.TOO_FAR_AHEAD
        return period

    def alternatives(self, request: BookingRequest, limit: int = 3) -> list[Check]:
        """The nearest bookable options to a request, closest in time first (UC-04)."""
        found: list[Check] = []
        for candidate in self._nearby_requests(request):
            check = self.check(candidate)
            if check.available:
                found.append(check)
                if len(found) == limit:
                    break
        return found

    # -- helpers ---------------------------------------------------------------------------

    @property
    def _slot_minutes(self) -> int:
        return _configured(self._rules.slot_minutes, "slot_minutes")

    def _duration_minutes(self, service: Service) -> int:
        duration = service.duration_minutes or self._rules.default_duration_minutes
        return _configured(duration, "default_duration_minutes")

    @staticmethod
    def _minute_of_day(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute

    def _aware(self, local: datetime) -> datetime:
        if local.tzinfo is not None:
            return local
        return local.replace(tzinfo=self._zone)

    def _within_opening_hours(self, start: datetime, end: datetime) -> bool:
        # An interval that runs past midnight belongs to the day it opens on.
        for day in (start.date() - timedelta(days=1), start.date()):
            for interval in self._profile.opening_hours.get(_weekday(day), []):
                opens = datetime.combine(day, interval.opens)
                closes_day = day + timedelta(days=1) if interval.crosses_midnight else day
                closes = datetime.combine(closes_day, interval.closes)
                if opens <= start and end <= closes:
                    return True
        return False

    def _candidates(self, service: Service, party_size: int) -> list[Resource]:
        """Resources that offer the service and fit the party, smallest first."""
        suitable = [
            resource
            for resource in self._profile.resources
            if resource.capacity >= party_size
            and (not resource.service_ids or service.id in resource.service_ids)
        ]
        return sorted(suitable, key=lambda resource: resource.capacity)

    def _free_units(self, resource: Resource, period: Period) -> int:
        """Units of `resource` free for all of `period`: quantity minus peak concurrent use."""
        overlapping = [p for p in self._taken.get(resource.id, ()) if p.overlaps(period)]
        changes: list[tuple[datetime, int]] = []
        for booked in overlapping:
            changes.append((max(booked.starts_at, period.starts_at), 1))
            changes.append((min(booked.ends_at, period.ends_at), -1))
        peak = in_use = 0
        # Ends sort before starts at the same instant, so back-to-back bookings don't clash.
        for _, delta in sorted(changes):
            in_use += delta
            peak = max(peak, in_use)
        return resource.quantity - peak

    def _nearby_requests(self, request: BookingRequest) -> list[BookingRequest]:
        start = request.start
        if self._rules.mode is BookingMode.OVERNIGHT:
            days = sorted(
                range(-ALTERNATIVE_SEARCH_DAYS, ALTERNATIVE_SEARCH_DAYS + 1),
                key=lambda d: (abs(d), d),
            )
            return [_replace_start(request, start + timedelta(days=d)) for d in days if d]

        step = timedelta(minutes=self._slot_minutes)
        first_day = datetime.combine(start.date() - timedelta(days=1), time())
        count = (ALTERNATIVE_SEARCH_DAYS + 2) * 24 * 60 // self._slot_minutes
        starts = [first_day + step * i for i in range(count)]
        starts.sort(key=lambda moment: (abs(moment - start), moment))
        return [_replace_start(request, moment) for moment in starts if moment != start]


def _configured[T](value: T | None, setting: str) -> T:
    # The profile schema guarantees these settings for the booking mode in use.
    if value is None:
        raise ValueError(f"profile has no {setting}")
    return value


_WEEKDAYS = list(Weekday)  # Monday first, like date.weekday()


def _weekday(day: date) -> Weekday:
    return _WEEKDAYS[day.weekday()]


def _replace_start(request: BookingRequest, start: datetime) -> BookingRequest:
    return BookingRequest(request.service_id, request.party_size, start, request.nights)
