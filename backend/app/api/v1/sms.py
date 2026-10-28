"""Twilio's webhook for text messages (FR-02-02; ADR-0011). Not for the dashboard: Twilio
calls it, and it must carry a valid `X-Twilio-Signature`.

    POST /sms/incoming     a text message arrived (the number's "A message comes in" webhook)

It always answers TwiML with HTTP 200: an error would make Twilio retry and log a failure,
and the customer would get nothing either way. An unexpected failure sends an apology.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.channels.dependencies import get_sms_service
from app.channels.sms.service import SmsService, message_twiml
from app.channels.twilio import twilio_params
from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/sms", tags=["sms"], include_in_schema=False)

TROUBLE = "Sorry, we can't answer messages right now. Please try again later or give us a call."


@router.post("/incoming")
async def incoming(
    params: Annotated[dict[str, str], Depends(twilio_params)],
    service: Annotated[SmsService, Depends(get_sms_service)],
) -> Response:
    try:
        reply = await service.incoming(params)
    except Exception:
        log.exception("sms_webhook_failed")
        reply = TROUBLE
    return Response(content=message_twiml(reply), media_type="application/xml")
