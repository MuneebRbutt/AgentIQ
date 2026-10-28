"""FastAPI dependencies for escalations."""

from typing import Annotated

from fastapi import Depends

from app.core.clock import Clock, get_clock
from app.db.session import DbSession
from app.escalations.service import EscalationService


def get_escalation_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> EscalationService:
    return EscalationService(db, clock)
