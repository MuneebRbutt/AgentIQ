"""Which phone number reaches which business profile (analysis M-5; ADR-0010).

Operators manage their own profile's numbers. Incoming calls look the dialled number up
across all tenants, because Twilio's signed request is the only thing a call carries.
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.voice.models import PhoneNumber
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.profiles.models import BusinessProfile
from app.providers.notifications import to_e164
from app.tenancy.scoping import scoped

NUMBER_NOT_FOUND = "Phone number not found."
NUMBER_TAKEN = "That number is already connected to a business profile."
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_number(raw: str, default_country_code: str) -> str:
    """The E.164 form Twilio uses, or a ValidationError the operator can act on."""
    number = to_e164(raw, default_country_code)
    if not _E164.match(number):
        raise ValidationError(
            "Enter the full phone number, e.g. +14155550123 or +92 42 1234567.",
            extra={"field": "number"},
        )
    return number


class PhoneNumberService:
    def __init__(self, db: AsyncSession, default_country_code: str) -> None:
        self._db = db
        self._country = default_country_code

    async def list_numbers(self, profile: BusinessProfile) -> list[PhoneNumber]:
        statement = (
            scoped(select(PhoneNumber), PhoneNumber, profile.tenant_id)
            .where(PhoneNumber.profile_id == profile.id)
            .order_by(PhoneNumber.created_at, PhoneNumber.number)
        )
        return list(await self._db.scalars(statement))

    async def add(self, profile: BusinessProfile, raw_number: str) -> PhoneNumber:
        number = normalize_number(raw_number, self._country)
        row = PhoneNumber(tenant_id=profile.tenant_id, profile_id=profile.id, number=number)
        self._db.add(row)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise ConflictError(NUMBER_TAKEN) from exc
        return row

    async def remove(self, profile: BusinessProfile, number_id: uuid.UUID) -> None:
        statement = scoped(select(PhoneNumber), PhoneNumber, profile.tenant_id).where(
            PhoneNumber.id == number_id, PhoneNumber.profile_id == profile.id
        )
        row = await self._db.scalar(statement)
        if row is None:
            raise NotFoundError(NUMBER_NOT_FOUND)
        await self._db.delete(row)
        await self._db.commit()

    async def route(self, dialled: str) -> PhoneNumber | None:
        """The number Twilio says was called (`To`), across tenants."""
        try:
            number = normalize_number(dialled, self._country)
        except ValidationError:
            return None
        return await self._db.scalar(select(PhoneNumber).where(PhoneNumber.number == number))
