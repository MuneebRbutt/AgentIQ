from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    auth,
    bookings,
    email_addresses,
    escalations,
    followups,
    health,
    knowledge,
    operators,
    phone_numbers,
    profiles,
    sessions,
    sms,
    voice,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(operators.router)
api_router.include_router(profiles.router)
api_router.include_router(bookings.router)
api_router.include_router(knowledge.router)
api_router.include_router(sessions.router)
api_router.include_router(escalations.router)
api_router.include_router(followups.router)
api_router.include_router(analytics.router)
api_router.include_router(phone_numbers.router)
api_router.include_router(email_addresses.router)
api_router.include_router(voice.router)
api_router.include_router(sms.router)
