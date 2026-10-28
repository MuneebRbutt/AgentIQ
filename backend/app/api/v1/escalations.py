"""Escalations of one of the caller's business profiles: conversations handed to a person
(FR-06-04, FR-08-03). The dashboard lists the open ones; operators mark them handled.

    GET   /profiles/{id}/escalations[?status=&session_id=]
    GET   /profiles/{id}/escalations/{eid}
    PATCH /profiles/{id}/escalations/{eid}     {"status": "resolved", "note": "..."}
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.profiles import CurrentProfile
from app.auth.dependencies import CurrentAuth
from app.escalations.api_schemas import EscalationOut, EscalationUpdate
from app.escalations.dependencies import get_escalation_service
from app.escalations.models import Escalation
from app.escalations.service import EscalationService, Status

router = APIRouter(prefix="/profiles/{profile_id}/escalations", tags=["escalations"])

Service = Annotated[EscalationService, Depends(get_escalation_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or escalation in your business"}
}


@router.get("", response_model=list[EscalationOut], responses=_NOT_FOUND)
async def list_escalations(
    profile: CurrentProfile,
    service: Service,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    session_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Escalation]:
    """Newest first."""
    return await service.list_recent(
        profile, status=status_filter, session_id=session_id, limit=limit, offset=offset
    )


@router.get("/{escalation_id}", response_model=EscalationOut, responses=_NOT_FOUND)
async def get_escalation(
    profile: CurrentProfile, escalation_id: uuid.UUID, service: Service
) -> Escalation:
    return await service.get(profile, escalation_id)


@router.patch("/{escalation_id}", response_model=EscalationOut, responses=_NOT_FOUND)
async def update_escalation(
    profile: CurrentProfile,
    escalation_id: uuid.UUID,
    body: EscalationUpdate,
    auth: CurrentAuth,
    service: Service,
) -> Escalation:
    return await service.update(
        profile, escalation_id, auth.operator_id, status=body.status, note=body.note
    )
