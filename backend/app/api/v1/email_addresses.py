"""Email addresses that reach one of the caller's business profiles (FR-02-03; ADR-0011).

GET    /profiles/{id}/email-addresses          addresses and the mailbox status
POST   /profiles/{id}/email-addresses          connect an address
DELETE /profiles/{id}/email-addresses/{aid}    disconnect it
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from app.api.v1.profiles import CurrentProfile
from app.channels.email.addresses import EmailAddressService
from app.channels.email.api_schemas import EmailAddressIn, EmailAddressOut, EmailSetupOut
from app.channels.email.models import EmailAddress
from app.core.config import Settings, get_settings
from app.db.session import DbSession

router = APIRouter(prefix="/profiles/{profile_id}/email-addresses", tags=["email addresses"])


def get_email_address_service(db: DbSession) -> EmailAddressService:
    return EmailAddressService(db)


Service = Annotated[EmailAddressService, Depends(get_email_address_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or address in your business"}
}


@router.get("", response_model=EmailSetupOut, responses=_NOT_FOUND)
async def list_email_addresses(
    profile: CurrentProfile,
    service: Service,
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmailSetupOut:
    addresses = await service.list_addresses(profile)
    return EmailSetupOut(
        addresses=[EmailAddressOut.model_validate(a) for a in addresses],
        inbound_enabled=settings.email_inbound_enabled,
        mailbox_address=settings.imap_username,
    )


@router.post(
    "",
    response_model=EmailAddressOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {"description": "The address is connected to a profile already"},
    },
)
async def add_email_address(
    profile: CurrentProfile, body: EmailAddressIn, service: Service
) -> EmailAddress:
    return await service.add(profile, body.address)


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def remove_email_address(
    profile: CurrentProfile, address_id: uuid.UUID, service: Service
) -> Response:
    await service.remove(profile, address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
