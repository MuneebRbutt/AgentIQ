"""Fixtures for tests against the docker compose services (`docker compose up -d`).

Integration tests run against a separate database, `<DATABASE_URL's name>_test` (or
`TEST_DATABASE_URL` if set), created and migrated on first use and emptied before every
test, so running `pytest` never touches development data.

When Postgres or Redis is unreachable the tests are skipped, so `pytest` stays green on a
machine without Docker. CI sets `REQUIRE_INTEGRATION=1` to turn that skip into a failure.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.models import Operator
from app.auth.service import AuthService
from app.core.clock import get_clock
from app.core.config import Environment, Settings
from app.core.redis import create_redis, ping_redis
from app.db.base import Base
from app.db.session import ping_database
from app.main import create_app
from app.profiles.examples import load_example
from app.profiles.models import BusinessProfile
from app.profiles.schema import Domain
from app.profiles.service import ProfileService
from app.tenancy.models import Tenant

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PASSWORD = "correct-horse-1"


def _test_database_url(settings: Settings) -> str:
    if explicit := os.environ.get("TEST_DATABASE_URL"):
        return explicit
    url = make_url(settings.database_url.get_secret_value())
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


async def _services_problem(settings: Settings) -> str | None:
    """Why Postgres or Redis can't be reached, or None if both answer."""
    engine = create_async_engine(settings.database_url.get_secret_value())
    redis = create_redis(settings)
    try:
        async with asyncio.timeout(5):
            await ping_redis(redis)
            await ping_database(engine)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        await redis.aclose()
        await engine.dispose()
    return None


async def _ensure_database(settings: Settings, test_url: str) -> None:
    admin = create_async_engine(
        settings.database_url.get_secret_value(), isolation_level="AUTOCOMMIT"
    )
    name = make_url(test_url).database
    try:
        async with admin.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()


def run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed command, no user input
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    base = Settings()  # the developer's .env / CI environment
    test_url = _test_database_url(base)
    problem = asyncio.run(_services_problem(base))
    if problem is not None:
        message = f"Postgres/Redis not reachable ({problem}); run `docker compose up -d`"
        if os.environ.get("REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)

    try:
        asyncio.run(_ensure_database(base, test_url))
    except Exception as exc:
        pytest.fail(
            f"Couldn't create the test database ({exc}). Create it yourself or point "
            "TEST_DATABASE_URL at an empty database."
        )

    migrated = run_alembic(test_url, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr
    # In-memory Chroma, the offline embedder and sentiment word list, and no real model.
    # Tests run the follow-up worker themselves, one pass at a time, with the fake clock.
    return Settings(
        database_url=test_url,
        environment=Environment.TEST,
        chroma_path="",
        embedding_provider="hashing",
        sentiment_provider="lexicon",
        llm_api_key=None,
        followup_worker_enabled=False,
        twilio_account_sid=None,
        smtp_host=None,
    )


@dataclass
class FakeClock:
    """Time stands still until a test moves it (TC-07-style clock control)."""

    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC).replace(microsecond=0))


@pytest.fixture
async def live_app(integration_settings: Settings, clock: FakeClock) -> AsyncIterator[FastAPI]:
    app = create_app(integration_settings)
    app.dependency_overrides[get_clock] = lambda: clock
    async with app.router.lifespan_context(app):
        tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        if tables:
            async with app.state.engine.begin() as connection:
                await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
        yield app


@pytest.fixture
async def live_client(live_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=live_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def db(live_app: FastAPI) -> AsyncIterator[AsyncSession]:
    async with live_app.state.session_factory() as session:
        yield session


@dataclass
class OperatorFactory:
    db: AsyncSession
    settings: Settings
    clock: FakeClock

    async def tenant(self, name: str = "Grand Hotel") -> Tenant:
        tenant = Tenant(id=uuid.uuid4(), name=name)
        self.db.add(tenant)
        await self.db.commit()
        return tenant

    async def operator(
        self,
        email: str,
        *,
        tenant: Tenant | None = None,
        password: str = DEFAULT_PASSWORD,
        full_name: str = "Test Operator",
    ) -> Operator:
        tenant = tenant or await self.tenant()
        service = AuthService(self.db, self.settings, self.clock)
        return await service.create_operator(
            tenant=tenant, email=email, full_name=full_name, password=password
        )


@pytest.fixture
def factory(db: AsyncSession, integration_settings: Settings, clock: FakeClock) -> OperatorFactory:
    return OperatorFactory(db, integration_settings, clock)


def next_tuesday(clock: FakeClock, *, at_least_days: int = 3) -> date:
    """A Tuesday a few days ahead, local to the example profiles (all three are open)."""
    day = clock.now().astimezone(ZoneInfo("Asia/Karachi")).date() + timedelta(days=at_least_days)
    return day + timedelta(days=(1 - day.weekday()) % 7)


@pytest.fixture
async def make_profile(
    db: AsyncSession, clock: FakeClock
) -> Callable[[Tenant, Domain], Awaitable[BusinessProfile]]:
    async def make(tenant: Tenant, domain: Domain) -> BusinessProfile:
        return await ProfileService(db, clock).create(tenant.id, load_example(domain))

    return make
