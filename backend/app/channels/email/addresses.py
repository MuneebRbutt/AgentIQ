"""Which address reaches which business profile (FR-02-03; ADR-0011).

Operators manage their own profile's addresses. An incoming email is looked up by its
recipients across all tenants, because the address it was sent to is the only trustworthy
thing it carries about the business (NFR03-02).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import validate_new_email
from app.channels.email.models import EmailAddress
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.profiles.models import BusinessProfile
from app.tenancy.scoping import scoped

ADDRESS_NOT_FOUND = "Email address not found."
ADDRESS_TAKEN = "That address is already connected to a business profile."


def normalize_address(raw: str) -> str:
    try:
        return validate_new_email(raw)
    except ValueError as exc:
        raise ValidationError(
            "Enter a full email address, e.g. agentiq+clinic@gmail.com.",
            extra={"field": "address"},
        ) from exc


class EmailAddressService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_addresses(self, profile: BusinessProfile) -> list[EmailAddress]:
        statement = (
            scoped(select(EmailAddress), EmailAddress, profile.tenant_id)
            .where(EmailAddress.profile_id == profile.id)
            .order_by(EmailAddress.created_at, EmailAddress.address)
        )
        return list(await self._db.scalars(statement))

    async def add(self, profile: BusinessProfile, raw_address: str) -> EmailAddress:
        address = normalize_address(raw_address)
        row = EmailAddress(tenant_id=profile.tenant_id, profile_id=profile.id, address=address)
        self._db.add(row)
        try:
            await self._db.commit()
        except IntegrityError as exc:
            await self._db.rollback()
            raise ConflictError(ADDRESS_TAKEN) from exc
        return row

    async def remove(self, profile: BusinessProfile, address_id: uuid.UUID) -> None:
        statement = scoped(select(EmailAddress), EmailAddress, profile.tenant_id).where(
            EmailAddress.id == address_id, EmailAddress.profile_id == profile.id
        )
        row = await self._db.scalar(statement)
        if row is None:
            raise NotFoundError(ADDRESS_NOT_FOUND)
        await self._db.delete(row)
        await self._db.commit()

    async def route(self, recipients: Iterable[str]) -> EmailAddress | None:
        """The first connected address among an email's recipients, across tenants."""
        wanted = [address.lower() for address in recipients]
        if not wanted:
            return None
        rows = {
            row.address: row
            for row in await self._db.scalars(
                select(EmailAddress).where(EmailAddress.address.in_(wanted))
            )
        }
        return next((rows[address] for address in wanted if address in rows), None)

    async def all_addresses(self) -> set[str]:
        """Every connected address, so the mailbox never answers itself."""
        return set(await self._db.scalars(select(EmailAddress.address)))
