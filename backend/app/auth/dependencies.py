"""FastAPI dependencies for authenticated routes (NFR03-03).

Add `CurrentAuth` to a route's parameters and it becomes login-only; use its `tenant_id`
to scope every query.
"""

from typing import Annotated

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import INVALID_SESSION, AuthContext, AuthService
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.db.session import DbSession

# auto_error=False so a missing header gets our problem-JSON 401, not FastAPI's 403.
_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login")


def get_auth_service(
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> AuthService:
    return AuthService(db, settings, clock)


async def get_current_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthContext:
    if credentials is None:
        raise AuthenticationError(INVALID_SESSION)
    context = await service.authenticate(credentials.credentials)
    structlog.contextvars.bind_contextvars(
        tenant_id=str(context.tenant_id), operator_id=str(context.operator_id)
    )
    return context


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]
