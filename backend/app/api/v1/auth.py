"""Operator login, logout and identity (FR-01-01…FR-01-04, UC-01)."""

from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from app.auth.dependencies import AuthServiceDep, CurrentAuth
from app.auth.models import Operator
from app.auth.schemas import LoginRequest, OperatorOut, TokenResponse
from app.core.errors import AuthenticationError
from app.db.session import DbSession

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Not logged in or session ended"}
}


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password"}},
)
async def login(body: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    result = await service.login(body.email, body.password)
    return TokenResponse(
        access_token=result.access_token,
        expires_at=result.expires_at,
        operator=OperatorOut.model_validate(result.operator),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_UNAUTHORIZED)
async def logout(auth: CurrentAuth, service: AuthServiceDep) -> None:
    await service.logout(auth)


@router.get("/me", response_model=OperatorOut, responses=_UNAUTHORIZED)
async def me(auth: CurrentAuth, db: DbSession) -> Operator:
    operator = await db.scalar(select(Operator).where(Operator.id == auth.operator_id))
    if operator is None:  # deleted between token check and now
        raise AuthenticationError("Your session has ended. Please log in again.")
    return operator
