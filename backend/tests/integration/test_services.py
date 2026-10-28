"""Integration tests for the backing services and migrations."""

import httpx
import pytest

from app.core.config import Settings
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration


async def test_health_reports_real_services_ok(live_client: httpx.AsyncClient) -> None:
    response = await live_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_alembic_upgrade_and_downgrade_run_cleanly(integration_settings: Settings) -> None:
    url = integration_settings.database_url.get_secret_value()
    for args in (["downgrade", "base"], ["upgrade", "head"], ["check"]):
        result = run_alembic(url, *args)
        assert result.returncode == 0, result.stderr
