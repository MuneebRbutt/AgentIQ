"""A fresh deployment gets its demo business once (FR-10-08, M12's three live demo
profiles; ADR-0012)."""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select

from app.auth.models import Operator
from app.cli import prepare
from app.core.config import Settings
from app.deployment import setup_demo
from app.profiles.models import BusinessProfile

pytestmark = pytest.mark.integration

EMAIL = "demo@agentiq.test"
PASSWORD = "Demo-pass-2026!"  # a test value, not a credential


async def test_the_demo_business_is_created_once(
    live_app: FastAPI, integration_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    first = await setup_demo(integration_settings, EMAIL, PASSWORD, "AgentIQ Demo")
    settings = integration_settings.model_copy(
        update={
            "demo_operator_email": EMAIL,
            "demo_operator_password": SecretStr("a-different-password-1"),
        }
    )
    await prepare(settings)  # as on every container start

    assert first.created_tenant and first.created_operator
    assert sorted(first.created_profiles) == [
        "CareWell Family Clinic",
        "Grand Lahore Hotel",
        "Spice Garden",
    ]
    assert "password unchanged" in capsys.readouterr().out
    async with live_app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Operator)) == 1
        assert await db.scalar(select(func.count()).select_from(BusinessProfile)) == 3

    transport = httpx.ASGITransport(app=live_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://internal") as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200  # the first password still works
