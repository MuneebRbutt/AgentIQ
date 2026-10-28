"""Getting a deployment ready (FR-10-08; ADR-0012).

The container runs `python -m app.cli prepare` before the API starts (backend/docker/start.sh):

- the knowledge search index is rebuilt from the documents' stored text when it is missing,
  as it is after every deploy on a host without a persistent disk;
- with `DEMO_OPERATOR_EMAIL` and `DEMO_OPERATOR_PASSWORD` set, the demo business is created
  once: an operator and the three example profiles (hotel, restaurant, clinic), so a fresh
  deployment can be logged into straight away (M12: three live demo profiles).

`prefetch_models` downloads the embedding and sentiment models while the image is built, so
a cold start never waits for a download.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.auth.models import Operator
from app.auth.service import AuthService
from app.core.clock import SystemClock
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import create_engine, create_session_factory
from app.knowledge.embeddings import create_embedder
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.providers.sentiment import download_model
from app.tenancy.models import Tenant

log = get_logger(__name__)

DEMO_OPERATOR_NAME = "Demo Operator"


def index_is_missing(settings: Settings) -> bool:
    """True when the knowledge index has no files yet (an in-memory index always needs it)."""
    directory = settings.chroma_directory
    return directory is None or not directory.is_dir() or not any(directory.iterdir())


def prefetch_models(settings: Settings) -> list[str]:
    """Downloads the configured models now; raises if one can't be fetched."""
    fetched: list[str] = []
    if settings.embedding_provider == "minilm":
        create_embedder(settings).embed(["warm up"])
        fetched.append("all-MiniLM-L6-v2")
    if settings.sentiment_provider == "model":
        directory = settings.sentiment_model_directory / settings.sentiment_model.replace("/", "--")
        download_model(
            settings.sentiment_model,
            settings.sentiment_model_file,
            directory,
            base_url=settings.sentiment_model_base_url,
        )
        fetched.append(settings.sentiment_model)
    return fetched


@dataclass
class DemoSetup:
    tenant_id: uuid.UUID
    created_tenant: bool = False
    created_operator: bool = False
    created_profiles: list[str] = field(default_factory=list)


async def setup_demo(settings: Settings, email: str, password: str, business: str) -> DemoSetup:
    """Creates whatever is missing of the demo business; running it again changes nothing.
    An existing operator keeps their password."""
    engine = create_engine(settings)
    clock = SystemClock()
    try:
        async with create_session_factory(engine)() as db:
            existing = await db.scalar(select(Operator).where(Operator.email == email.lower()))
            if existing is not None:
                tenant = await db.get(Tenant, existing.tenant_id)
                assert tenant is not None  # noqa: S101  # operators belong to a tenant
                setup = DemoSetup(tenant.id)
            else:
                tenant = await db.scalar(select(Tenant).where(Tenant.name == business))
                setup = DemoSetup(tenant.id if tenant else uuid.uuid4())
                if tenant is None:
                    tenant = Tenant(id=setup.tenant_id, name=business)
                    db.add(tenant)
                    await db.flush()
                    setup.created_tenant = True
                await AuthService(db, settings, clock).create_operator(
                    tenant=tenant, email=email, full_name=DEMO_OPERATOR_NAME, password=password
                )
                setup.created_operator = True
            profiles = await db.scalar(
                select(func.count())
                .select_from(BusinessProfile)
                .where(BusinessProfile.tenant_id == tenant.id)
            )
            if not profiles:
                service = ProfileService(db, clock)
                for domain in Domain:
                    profile = await service.create(tenant.id, load_example(domain))
                    setup.created_profiles.append(profile.name)
    finally:
        await engine.dispose()
    log.info(
        "demo_ready",
        tenant_id=str(setup.tenant_id),
        created_operator=setup.created_operator,
        created_profiles=len(setup.created_profiles),
    )
    return setup
