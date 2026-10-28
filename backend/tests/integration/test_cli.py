"""The account setup command against a real database (AS-4: no public sign-up)."""

import argparse
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.core.config import Settings
from app.knowledge.embeddings import create_embedder
from app.knowledge.service import KnowledgeService, Upload
from app.knowledge.store import ChromaVectorStore
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.tenancy.models import Tenant
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory

MakeProfile = Callable[[Tenant, Domain], Awaitable[BusinessProfile]]

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("live_app")]


def _create_args(*owner: str, email: str = "a@clinic.test") -> argparse.Namespace:
    return cli.build_parser().parse_args(
        ["create-operator", *owner, "--email", email, "--name", "Sara"]
    )


async def test_create_operator_with_a_new_business_then_list_it(
    integration_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _create_args("--business", " Grand Hotel ", email="Sara@Hotel.test")

    await cli.create_operator(integration_settings, args, DEFAULT_PASSWORD)
    await cli.list_tenants(integration_settings)

    out = capsys.readouterr().out
    assert "Created operator sara@hotel.test" in out
    assert "Grand Hotel  (1 operators)" in out


async def test_create_operator_in_an_existing_business(
    integration_settings: Settings, factory: OperatorFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    tenant = await factory.tenant("City Clinic")

    await cli.create_operator(
        integration_settings, _create_args("--tenant-id", str(tenant.id)), DEFAULT_PASSWORD
    )

    assert f"tenant id {tenant.id}" in capsys.readouterr().out


async def test_create_operator_with_an_unknown_business_id_stops(
    integration_settings: Settings,
) -> None:
    args = _create_args("--tenant-id", str(uuid.uuid4()))

    with pytest.raises(SystemExit, match="No tenant"):
        await cli.create_operator(integration_settings, args, DEFAULT_PASSWORD)


async def test_seed_profiles_adds_the_examples(
    integration_settings: Settings, factory: OperatorFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    tenant = await factory.tenant("Demo Business")

    await cli.seed_profiles(integration_settings, tenant.id, [Domain.CLINIC, Domain.HOTEL])

    out = capsys.readouterr().out
    assert "Created clinic profile 'CareWell Family Clinic'" in out
    assert "Created hotel profile 'Grand Lahore Hotel'" in out


async def test_seed_profiles_needs_an_existing_business(integration_settings: Settings) -> None:
    with pytest.raises(SystemExit, match="No tenant"):
        await cli.seed_profiles(integration_settings, uuid.uuid4(), [Domain.CLINIC])


async def test_reindex_knowledge_rebuilds_a_lost_index(
    integration_settings: Settings,
    factory: OperatorFactory,
    make_profile: MakeProfile,
    db: AsyncSession,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tenant = await factory.tenant("Docs Business")
    profile = await make_profile(tenant, Domain.HOTEL)
    original = integration_settings.model_copy(update={"chroma_path": str(tmp_path / "old")})
    embedder = create_embedder(original)
    service = KnowledgeService(
        db,
        FakeClock(datetime.now(UTC)),
        original,
        ChromaVectorStore(tmp_path / "old", "hashing"),
        embedder,
    )
    upload = Upload("faq.txt", "text/plain", b"Breakfast is at 7. Checkout is at noon.")
    await service.upload(profile, upload)

    # The index folder is gone (say, a new server); the text in Postgres rebuilds it.
    fresh = integration_settings.model_copy(update={"chroma_path": str(tmp_path / "new")})
    await cli.reindex_knowledge(fresh, tenant.id)

    assert "1 passages" in capsys.readouterr().out
    assert await ChromaVectorStore(tmp_path / "new", "hashing").count(tenant.id) == 1


def test_reindex_knowledge_parses_an_optional_tenant() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["reindex-knowledge"]).tenant_id is None
    tenant_id = uuid.uuid4()
    assert parser.parse_args(["reindex-knowledge", "--tenant-id", str(tenant_id)]).tenant_id == (
        tenant_id
    )
