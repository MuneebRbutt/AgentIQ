"""Follow-up messages of one of the caller's business profiles: confirmations, reminders,
feedback requests and escalation-notice retries (FR-07, FR-08-03; ADR-0007).

    GET  /profiles/{id}/follow-ups[?status=&kind=&booking_id=&session_id=]
    GET  /profiles/{id}/follow-ups/{fid}
    POST /profiles/{id}/follow-ups/{fid}/cancel      only while still pending
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.profiles import CurrentProfile
from app.followups.api_schemas import FollowUpOut
from app.followups.dependencies import get_follow_up_service
from app.followups.models import FollowUpTask
from app.followups.service import FollowUpService, Kind, Status

router = APIRouter(prefix="/profiles/{profile_id}/follow-ups", tags=["follow-ups"])

Service = Annotated[FollowUpService, Depends(get_follow_up_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or follow-up in your business"}
}


@router.get("", response_model=list[FollowUpOut], responses=_NOT_FOUND)
async def list_follow_ups(
    profile: CurrentProfile,
    service: Service,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    kind: Kind | None = None,
    booking_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FollowUpTask]:
    """Latest due first."""
    return await service.list_recent(
        profile,
        status=status_filter,
        kind=kind,
        booking_id=booking_id,
        session_id=session_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{follow_up_id}", response_model=FollowUpOut, responses=_NOT_FOUND)
async def get_follow_up(
    profile: CurrentProfile, follow_up_id: uuid.UUID, service: Service
) -> FollowUpTask:
    return await service.get(profile, follow_up_id)


@router.post(
    "/{follow_up_id}/cancel",
    response_model=FollowUpOut,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {"description": "Already sent or failed"},
    },
)
async def cancel_follow_up(
    profile: CurrentProfile, follow_up_id: uuid.UUID, service: Service
) -> FollowUpTask:
    return await service.cancel(profile, follow_up_id)
