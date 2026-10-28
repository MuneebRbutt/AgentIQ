"""Operators of the caller's own tenant. The first tenant-scoped resource (NFR03-02)."""

import uuid

from fastapi import APIRouter, status
from sqlalchemy import select

from app.auth.dependencies import CurrentAuth
from app.auth.models import Operator
from app.auth.schemas import OperatorOut
from app.core.errors import NotFoundError
from app.db.session import DbSession
from app.tenancy.scoping import scoped

router = APIRouter(prefix="/operators", tags=["operators"])


@router.get("", response_model=list[OperatorOut])
async def list_operators(auth: CurrentAuth, db: DbSession) -> list[Operator]:
    statement = scoped(select(Operator), Operator, auth.tenant_id).order_by(Operator.created_at)
    return list(await db.scalars(statement))


@router.get(
    "/{operator_id}",
    response_model=OperatorOut,
    responses={status.HTTP_404_NOT_FOUND: {"description": "No such operator in your business"}},
)
async def get_operator(operator_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> Operator:
    statement = scoped(select(Operator), Operator, auth.tenant_id).where(Operator.id == operator_id)
    operator = await db.scalar(statement)
    if operator is None:
        raise NotFoundError("Operator not found.")
    return operator
