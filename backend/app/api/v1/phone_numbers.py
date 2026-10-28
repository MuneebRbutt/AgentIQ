"""Phone numbers that reach one of the caller's business profiles by call or text message
(analysis M-5, FR-02-01/02; ADR-0010, ADR-0011).

    GET    /profiles/{id}/phone-numbers          numbers and the Twilio webhook URLs
    POST   /profiles/{id}/phone-numbers          connect a number
    DELETE /profiles/{id}/phone-numbers/{nid}    disconnect it
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.v1.profiles import CurrentProfile
from app.channels.twilio import public_base_url
from app.channels.voice.api_schemas import PhoneNumberIn, PhoneNumberOut, PhoneSetupOut
from app.channels.voice.dependencies import get_phone_number_service
from app.channels.voice.models import PhoneNumber
from app.channels.voice.numbers import PhoneNumberService
from app.channels.voice.service import VoiceUrls
from app.core.config import Settings, get_settings

router = APIRouter(prefix="/profiles/{profile_id}/phone-numbers", tags=["phone numbers"])

Service = Annotated[PhoneNumberService, Depends(get_phone_number_service)]

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such profile or number in your business"}
}


@router.get("", response_model=PhoneSetupOut, responses=_NOT_FOUND)
async def list_phone_numbers(
    profile: CurrentProfile,
    service: Service,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> PhoneSetupOut:
    base = VoiceUrls(public_base_url(request, settings)).base
    numbers = await service.list_numbers(profile)
    return PhoneSetupOut(
        numbers=[PhoneNumberOut.model_validate(n) for n in numbers],
        incoming_webhook_url=f"{base}/api/v1/voice/incoming",
        status_callback_url=f"{base}/api/v1/voice/status",
        sms_webhook_url=f"{base}/api/v1/sms/incoming",
        signatures_checked=settings.twilio_auth_token is not None
        and settings.twilio_validate_signature,
    )


@router.post(
    "",
    response_model=PhoneNumberOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_NOT_FOUND,
        status.HTTP_409_CONFLICT: {"description": "The number is connected to a profile already"},
    },
)
async def add_phone_number(
    profile: CurrentProfile, body: PhoneNumberIn, service: Service
) -> PhoneNumber:
    return await service.add(profile, body.number)


@router.delete("/{number_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def remove_phone_number(
    profile: CurrentProfile, number_id: uuid.UUID, service: Service
) -> Response:
    await service.remove(profile, number_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
