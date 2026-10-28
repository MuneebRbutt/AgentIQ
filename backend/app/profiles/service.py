"""Create, read, edit and delete business profiles, always within one tenant (FR-08-04/06).

Every save stores a numbered snapshot. An edit names the version it was based on and is
refused if someone saved in between, so two operators can't silently overwrite each
other's changes.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import AuthContext
from app.core.clock import Clock
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.profiles.models import BusinessProfile, BusinessProfileVersion
from app.profiles.schema import BusinessProfileConfig
from app.tenancy.scoping import scoped

log = get_logger(__name__)

PROFILE_NOT_FOUND = "Business profile not found."


def _stored(config: BusinessProfileConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


class ProfileService:
    def __init__(self, db: AsyncSession, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def list_all(self, tenant_id: uuid.UUID) -> list[BusinessProfile]:
        statement = scoped(select(BusinessProfile), BusinessProfile, tenant_id)
        return list(await self._db.scalars(statement.order_by(BusinessProfile.name)))

    async def get(
        self, tenant_id: uuid.UUID, profile_id: uuid.UUID, *, for_update: bool = False
    ) -> BusinessProfile:
        statement = scoped(select(BusinessProfile), BusinessProfile, tenant_id).where(
            BusinessProfile.id == profile_id
        )
        if for_update:
            statement = statement.with_for_update()
        profile = await self._db.scalar(statement)
        if profile is None:
            raise NotFoundError(PROFILE_NOT_FOUND)
        return profile

    async def create(
        self,
        tenant_id: uuid.UUID,
        config: BusinessProfileConfig,
        *,
        saved_by: uuid.UUID | None = None,
    ) -> BusinessProfile:
        now = self._clock.now()
        profile = BusinessProfile(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=config.name,
            domain=config.domain.value,
            config=_stored(config),
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._db.add(profile)
        self._db.add(self._snapshot(profile, saved_by))
        await self._db.commit()
        log.info("profile_created", profile_id=str(profile.id), domain=profile.domain)
        return profile

    async def update(
        self,
        auth: AuthContext,
        profile_id: uuid.UUID,
        config: BusinessProfileConfig,
        *,
        expected_version: int,
    ) -> BusinessProfile:
        profile = await self.get(auth.tenant_id, profile_id, for_update=True)
        current_version = profile.version
        if current_version != expected_version:
            await self._db.rollback()  # release the row lock
            raise ConflictError(
                "This profile was changed by someone else. Reload it and apply your changes again.",
                extra={"current_version": current_version},
            )
        # UC-08 alternative path: the domain may change too; nothing else depends on it.
        profile.name = config.name
        profile.domain = config.domain.value
        profile.config = _stored(config)
        profile.version += 1
        profile.updated_at = self._clock.now()
        self._db.add(self._snapshot(profile, auth.operator_id))
        await self._db.commit()
        log.info("profile_updated", profile_id=str(profile.id), version=profile.version)
        return profile

    async def delete(self, tenant_id: uuid.UUID, profile_id: uuid.UUID) -> None:
        profile = await self.get(tenant_id, profile_id)
        await self._db.delete(profile)
        await self._db.commit()
        log.info("profile_deleted", profile_id=str(profile_id))

    async def versions(
        self, tenant_id: uuid.UUID, profile_id: uuid.UUID
    ) -> list[BusinessProfileVersion]:
        await self.get(tenant_id, profile_id)  # 404 unless it's this tenant's
        statement = (
            select(BusinessProfileVersion)
            .where(BusinessProfileVersion.profile_id == profile_id)
            .order_by(BusinessProfileVersion.version.desc())
        )
        return list(await self._db.scalars(statement))

    def _snapshot(
        self, profile: BusinessProfile, saved_by: uuid.UUID | None
    ) -> BusinessProfileVersion:
        return BusinessProfileVersion(
            id=uuid.uuid4(),
            profile_id=profile.id,
            version=profile.version,
            config=profile.config,
            saved_by=saved_by,
            created_at=profile.updated_at,
        )
