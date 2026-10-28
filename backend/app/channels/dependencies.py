"""FastAPI dependencies for the text-message channel (ADR-0011)."""

from typing import Annotated

from fastapi import Depends, Request

from app.channels.inbound import InboundHandler
from app.channels.sms.service import SmsService
from app.conversations.dependencies import get_orchestrator
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.orchestrator.service import Orchestrator


def get_inbound_handler(
    request: Request,
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    orchestrator: Annotated[Orchestrator, Depends(get_orchestrator)],
) -> InboundHandler:
    return InboundHandler(request.app.state.session_factory, clock, settings, orchestrator)


def get_sms_service(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    handler: Annotated[InboundHandler, Depends(get_inbound_handler)],
) -> SmsService:
    return SmsService(request.app.state.session_factory, settings, handler)
