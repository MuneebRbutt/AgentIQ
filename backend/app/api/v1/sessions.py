"""Conversations with one of the caller's business profiles: the text test console
(FR-08-02), conversation logs with sentiment and escalations (FR-08-03, FR-06-03) and
per-stage response times (FR-10-02).

    POST /profiles/{id}/sessions                     start a conversation
    POST /profiles/{id}/sessions/{sid}/messages      send a customer message, get the reply
    GET  /profiles/{id}/sessions[?status=]           conversations, most recent first
    GET  /profiles/{id}/sessions/{sid}               one conversation with every turn
    POST /profiles/{id}/sessions/{sid}/close         end it
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.v1.profiles import CurrentProfile
from app.auth.dependencies import CurrentAuth
from app.conversations.api_schemas import (
    MessageIn,
    MessageOut,
    SentimentOut,
    SessionCreate,
    SessionDetail,
    SessionOut,
    StartedSessionOut,
    TurnOut,
)
from app.conversations.dependencies import get_conversation_service, get_orchestrator
from app.conversations.models import Interaction
from app.conversations.service import ConversationService, Status
from app.escalations.api_schemas import EscalationOut
from app.escalations.dependencies import get_escalation_service
from app.escalations.service import EscalationService
from app.orchestrator.service import NormalizedRequest, Orchestrator
from app.profiles.schema import BusinessProfileConfig

router = APIRouter(prefix="/profiles/{profile_id}/sessions", tags=["conversations"])

Service = Annotated[ConversationService, Depends(get_conversation_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or conversation in your business"}
}


@router.post("", response_model=StartedSessionOut, status_code=status.HTTP_201_CREATED)
async def start_session(
    profile: CurrentProfile, body: SessionCreate, service: Service
) -> StartedSessionOut:
    interaction = await service.start(profile, "text", body.customer_phone)
    greeting = BusinessProfileConfig.model_validate(profile.config).assistant.greeting
    return StartedSessionOut.model_validate(
        {**SessionOut.model_validate(interaction).model_dump(), "greeting": greeting}
    )


@router.post(
    "/{session_id}/messages",
    response_model=MessageOut,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {"description": "The conversation was handed over or closed"},
    },
)
async def send_message(
    profile: CurrentProfile,
    session_id: uuid.UUID,
    body: MessageIn,
    auth: CurrentAuth,
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
) -> MessageOut:
    outcome = await orchestrator.handle(
        NormalizedRequest(
            tenant_id=auth.tenant_id,
            profile_id=profile.id,
            session_id=session_id,
            channel="text",
            text=body.text,
        )
    )
    return MessageOut(
        session_id=outcome.session_id,
        reply=outcome.reply,
        outcome=outcome.outcome,
        handed_off=outcome.handed_off,
        results=outcome.results,
        sentiment=outcome.sentiment,
        escalation_id=outcome.escalation_id,
        timings=outcome.timings,
    )


@router.get("", response_model=list[SessionOut], responses=_NOT_FOUND)
async def list_sessions(
    profile: CurrentProfile,
    service: Service,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Interaction]:
    return await service.list_recent(profile, status=status_filter, limit=limit, offset=offset)


@router.get("/{session_id}", response_model=SessionDetail, responses=_NOT_FOUND)
async def get_session(
    profile: CurrentProfile,
    session_id: uuid.UUID,
    service: Service,
    escalations: Annotated[EscalationService, Depends(get_escalation_service)],
) -> SessionDetail:
    """Every turn, how each customer message sounded, and any escalation (FR-08-03)."""
    interaction = await service.get(profile, session_id)
    return SessionDetail(
        **SessionOut.model_validate(interaction).model_dump(),
        turns=[TurnOut.model_validate(turn) for turn in await service.turns(interaction)],
        sentiment=[
            SentimentOut.model_validate(record) for record in await service.sentiment(interaction)
        ],
        escalations=[
            EscalationOut.model_validate(escalation)
            for escalation in await escalations.list_recent(profile, session_id=interaction.id)
        ],
    )


@router.post("/{session_id}/close", response_model=SessionOut, responses=_NOT_FOUND)
async def close_session(
    profile: CurrentProfile, session_id: uuid.UUID, service: Service
) -> Interaction:
    return await service.close(profile, session_id)
