"""Business profiles of the caller's business (FR-08-04/05/06, FR-09-04; UC-08)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, status
from pydantic import ValidationError

from app.auth.dependencies import CurrentAuth
from app.core.clock import Clock, get_clock
from app.db.session import DbSession
from app.knowledge.dependencies import get_vector_store
from app.knowledge.store import ChromaVectorStore
from app.profiles.api_schemas import (
    ProfileOut,
    ProfileSummary,
    ProfileUpdate,
    ProfileVersionOut,
    TemplateSummary,
    ValidationReport,
    field_errors,
)
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile, BusinessProfileVersion
from app.profiles.schema import BusinessProfileConfig, Domain
from app.profiles.service import ProfileService

router = APIRouter(prefix="/profiles", tags=["profiles"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile in your business"}
}


def get_profile_service(
    db: DbSession, clock: Annotated[Clock, Depends(get_clock)]
) -> ProfileService:
    return ProfileService(db, clock)


Service = Annotated[ProfileService, Depends(get_profile_service)]


async def get_current_profile(
    profile_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> BusinessProfile:
    """The profile in the path, if it belongs to the caller's business (else 404)."""
    return await service.get(auth.tenant_id, profile_id)


# For routes nested under /profiles/{profile_id} (bookings, knowledge).
CurrentProfile = Annotated[BusinessProfile, Depends(get_current_profile)]


# Fixed paths come before /{profile_id} so they aren't read as an ID.
@router.get("/templates", response_model=list[TemplateSummary])
async def list_templates(_: CurrentAuth) -> list[TemplateSummary]:
    return [TemplateSummary(domain=domain, name=load_example(domain).name) for domain in Domain]


@router.get("/templates/{domain}", response_model=BusinessProfileConfig)
async def get_template(domain: Domain, _: CurrentAuth) -> BusinessProfileConfig:
    """A complete example profile to start from (the dashboard's "start from template")."""
    return load_example(domain)


@router.post("/validate", response_model=ValidationReport)
async def validate_profile(
    _: CurrentAuth, config: Annotated[dict[str, Any], Body()]
) -> ValidationReport:
    """Check a profile without saving it, so the dashboard can highlight problems (FR-08-05)."""
    try:
        BusinessProfileConfig.model_validate(config)
    except ValidationError as exc:
        return ValidationReport(valid=False, errors=field_errors(list(exc.errors())))
    return ValidationReport(valid=True, errors=[])


@router.get("", response_model=list[ProfileSummary])
async def list_profiles(auth: CurrentAuth, service: Service) -> list[BusinessProfile]:
    return await service.list_all(auth.tenant_id)


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    config: BusinessProfileConfig, auth: CurrentAuth, service: Service
) -> BusinessProfile:
    return await service.create(auth.tenant_id, config, saved_by=auth.operator_id)


@router.get("/{profile_id}", response_model=ProfileOut, responses=_NOT_FOUND)
async def get_profile(
    profile_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> BusinessProfile:
    return await service.get(auth.tenant_id, profile_id)


@router.put(
    "/{profile_id}",
    response_model=ProfileOut,
    responses={**_NOT_FOUND, status.HTTP_409_CONFLICT: {"description": "Edited by someone else"}},
)
async def update_profile(
    profile_id: uuid.UUID, body: ProfileUpdate, auth: CurrentAuth, service: Service
) -> BusinessProfile:
    return await service.update(auth, profile_id, body.config, expected_version=body.version)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_profile(
    profile_id: uuid.UUID,
    auth: CurrentAuth,
    service: Service,
    store: Annotated[ChromaVectorStore, Depends(get_vector_store)],
) -> None:
    await service.delete(auth.tenant_id, profile_id)
    # Its documents went with it (cascade); remove their passages from the index too.
    await store.delete_profile(auth.tenant_id, profile_id)


@router.get("/{profile_id}/versions", response_model=list[ProfileVersionOut], responses=_NOT_FOUND)
async def list_versions(
    profile_id: uuid.UUID, auth: CurrentAuth, service: Service
) -> list[BusinessProfileVersion]:
    return await service.versions(auth.tenant_id, profile_id)
