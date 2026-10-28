"""Injectable clock, so time-dependent rules (token expiry, lockouts, reminders) are testable."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware (UTC)."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_system_clock = SystemClock()


def get_clock() -> Clock:
    """FastAPI dependency. Tests override it with a fixed or advancing clock."""
    return _system_clock
