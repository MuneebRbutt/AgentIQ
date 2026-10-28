"""FastAPI dependencies for follow-ups."""

from typing import Annotated

from fastapi import Depends

from app.core.clock import Clock, get_clock
from app.db.session import DbSession
from app.followups.service import FollowUpService


def get_follow_up_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> FollowUpService:
    return FollowUpService(db, clock)
