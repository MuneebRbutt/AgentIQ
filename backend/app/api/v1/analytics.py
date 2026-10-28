"""The overview of one of the caller's business profiles (FR-08-03, FR-10-02).

GET /profiles/{id}/analytics[?days=30]
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.analytics.service import Analytics, AnalyticsService
from app.api.v1.profiles import CurrentProfile
from app.core.clock import Clock, get_clock
from app.db.session import DbSession

router = APIRouter(prefix="/profiles/{profile_id}/analytics", tags=["analytics"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile in your business"}
}


def get_analytics_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> AnalyticsService:
    return AnalyticsService(db, clock)


@router.get("", response_model=Analytics, responses=_NOT_FOUND)
async def get_analytics(
    profile: CurrentProfile,
    service: Annotated[AnalyticsService, Depends(get_analytics_service)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Analytics:
    """Conversations, task outcomes, reply times, sentiment, escalations, bookings and
    follow-ups over the last `days` days."""
    return await service.overview(profile, days)
