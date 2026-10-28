import asyncio

import httpx
from fastapi import FastAPI

from app.api.v1.health import Probe, get_health_probes
from app.core.config import Settings, get_settings


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("connection refused")


async def hangs() -> None:
    await asyncio.sleep(10)


def use_probes(app: FastAPI, probes: dict[str, Probe]) -> None:
    app.dependency_overrides[get_health_probes] = lambda: probes


async def test_live_does_not_touch_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ok_when_all_probes_pass(app: FastAPI, client: httpx.AsyncClient) -> None:
    use_probes(app, {"database": ok, "redis": ok})

    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": app.version,
        "checks": {"database": "ok", "redis": "ok"},
    }


async def test_health_is_503_when_a_dependency_is_down(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    use_probes(app, {"database": broken, "redis": ok})

    response = await client.get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"database": "unavailable", "redis": "ok"}


async def test_hanging_dependency_times_out_instead_of_blocking(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    # NFR04-01: a stuck service must not hang the caller.
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"health_check_timeout_seconds": 0.05}
    )
    use_probes(app, {"database": hangs, "redis": ok})

    response = await asyncio.wait_for(client.get("/api/v1/health"), timeout=2)

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


async def test_health_error_details_are_not_leaked(app: FastAPI, client: httpx.AsyncClient) -> None:
    use_probes(app, {"database": broken})

    response = await client.get("/api/v1/health")

    assert "connection refused" not in response.text
