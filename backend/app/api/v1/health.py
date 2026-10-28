"""Health endpoints.

- `GET /api/v1/health/live`: the process is up. No dependencies touched.
- `GET /api/v1/health`: readiness. Probes every backing service with a timeout and
  returns 503 if any is unavailable, so a dead database never hangs the check (NFR04-01).
"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.redis import ping_redis
from app.db.session import ping_database

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)

Probe = Callable[[], Awaitable[None]]


class CheckStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class OverallStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class LiveResponse(BaseModel):
    status: OverallStatus


class HealthResponse(BaseModel):
    status: OverallStatus
    version: str
    checks: dict[str, CheckStatus]


def get_health_probes(request: Request) -> dict[str, Probe]:
    """Named dependency probes. Overridden in tests."""
    state = request.app.state
    return {
        "database": lambda: ping_database(state.engine),
        "redis": lambda: ping_redis(state.redis),
    }


async def _run_probe(name: str, probe: Probe, limit_seconds: float) -> CheckStatus:
    try:
        async with asyncio.timeout(limit_seconds):
            await probe()
    except Exception as exc:  # any failure, including timeout, means "unavailable"
        log.warning("health_probe_failed", check=name, error=type(exc).__name__)
        return CheckStatus.UNAVAILABLE
    return CheckStatus.OK


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse(status=OverallStatus.OK)


@router.get(
    "",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    request: Request,
    response: Response,
    probes: Annotated[dict[str, Probe], Depends(get_health_probes)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    names = list(probes)
    results = await asyncio.gather(
        *(_run_probe(name, probes[name], settings.health_check_timeout_seconds) for name in names)
    )
    checks = dict(zip(names, results, strict=True))

    healthy = all(result is CheckStatus.OK for result in results)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=OverallStatus.OK if healthy else OverallStatus.DEGRADED,
        version=request.app.version,
        checks=checks,
    )
