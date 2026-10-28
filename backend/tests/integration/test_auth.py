"""Operator login, sessions and lockout against a real database (FR-01, TC-01)."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Operator
from app.auth.service import INVALID_CREDENTIALS, INVALID_SESSION
from app.core.config import Settings
from app.core.errors import ConflictError, ValidationError
from tests.integration.conftest import DEFAULT_PASSWORD, FakeClock, OperatorFactory

pytestmark = pytest.mark.integration

EMAIL = "owner@grand.example.com"


async def _login(
    client: httpx.AsyncClient, email: str = EMAIL, password: str = DEFAULT_PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _token(client: httpx.AsyncClient, email: str = EMAIL) -> str:
    response = await _login(client, email)
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_login_rejected(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["detail"] == INVALID_CREDENTIALS
    assert response.headers["www-authenticate"] == "Bearer"


async def test_tc01_login_me_and_logout(
    live_client: httpx.AsyncClient, factory: OperatorFactory
) -> None:
    operator = await factory.operator(EMAIL, full_name="Sara Khan")

    response = await _login(live_client, "  Owner@Grand.Example.com ")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["operator"]["id"] == str(operator.id)
    assert body["operator"]["email"] == EMAIL
    assert "password_hash" not in body["operator"]

    token = body["access_token"]
    me = await live_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json()["full_name"] == "Sara Khan"
    assert me.json()["last_login_at"] is not None

    logout = await live_client.post("/api/v1/auth/logout", headers=_bearer(token))
    assert logout.status_code == 204

    # FR-01-04: the same token no longer works after logout.
    after = await live_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert after.status_code == 401
    assert after.json()["detail"] == INVALID_SESSION


async def test_tc01_wrong_password_and_unknown_email_get_the_same_answer(
    live_client: httpx.AsyncClient, factory: OperatorFactory
) -> None:
    await factory.operator(EMAIL)

    wrong_password = await _login(live_client, password="wrong-password")
    unknown_email = await _login(live_client, email="nobody@grand.example.com")
    not_an_email = await _login(live_client, email="not-an-email")

    for response in (wrong_password, unknown_email, not_an_email):
        _assert_login_rejected(response)
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


async def test_account_locks_after_repeated_failures_then_recovers(
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    await factory.operator(EMAIL)

    for _ in range(integration_settings.login_max_failed_attempts):
        _assert_login_rejected(await _login(live_client, password="wrong-password"))

    # Locked: even the right password gets the generic answer.
    _assert_login_rejected(await _login(live_client))

    clock.advance(minutes=integration_settings.login_lockout_minutes, seconds=1)
    assert (await _login(live_client)).status_code == 200


async def test_successful_login_resets_the_failure_count(
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    db: AsyncSession,
    integration_settings: Settings,
) -> None:
    operator = await factory.operator(EMAIL)

    for _ in range(integration_settings.login_max_failed_attempts - 1):
        await _login(live_client, password="wrong-password")
    assert (await _login(live_client)).status_code == 200

    await db.refresh(operator)
    assert operator.failed_login_attempts == 0
    assert operator.locked_until is None


async def test_inactive_operator_cannot_log_in_and_loses_open_sessions(
    live_client: httpx.AsyncClient, factory: OperatorFactory, db: AsyncSession
) -> None:
    operator = await factory.operator(EMAIL)
    token = await _token(live_client)

    operator.is_active = False
    await db.commit()

    _assert_login_rejected(await _login(live_client))
    assert (await live_client.get("/api/v1/auth/me", headers=_bearer(token))).status_code == 401


async def test_session_expires_after_the_token_lifetime(
    live_client: httpx.AsyncClient,
    factory: OperatorFactory,
    clock: FakeClock,
    integration_settings: Settings,
) -> None:
    await factory.operator(EMAIL)
    token = await _token(live_client)

    clock.advance(minutes=integration_settings.access_token_ttl_minutes - 1)
    assert (await live_client.get("/api/v1/auth/me", headers=_bearer(token))).status_code == 200

    clock.advance(minutes=1)
    expired = await live_client.get("/api/v1/auth/me", headers=_bearer(token))
    assert expired.status_code == 401
    assert expired.json()["detail"] == INVALID_SESSION


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic b3duZXI6cGFzcw=="},
    ],
    ids=["missing", "garbage", "wrong-scheme"],
)
@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/operators"])
async def test_protected_routes_need_a_valid_token(
    live_client: httpx.AsyncClient, path: str, headers: dict[str, str]
) -> None:
    response = await live_client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_create_operator_stores_a_lowercase_email_and_a_hash(
    factory: OperatorFactory, db: AsyncSession
) -> None:
    await factory.operator("  Mixed.Case@Grand.Example.com ")

    stored = await db.scalar(select(Operator))
    assert stored is not None
    assert stored.email == "mixed.case@grand.example.com"
    assert stored.password_hash.startswith("$2")
    assert DEFAULT_PASSWORD not in stored.password_hash


async def test_create_operator_rejects_a_duplicate_email(factory: OperatorFactory) -> None:
    await factory.operator(EMAIL)

    with pytest.raises(ConflictError):
        await factory.operator(EMAIL.upper(), tenant=await factory.tenant("Other Business"))


@pytest.mark.parametrize(
    ("email", "password"),
    [("not-an-email", DEFAULT_PASSWORD), (EMAIL, "short")],
    ids=["bad-email", "weak-password"],
)
async def test_create_operator_validates_input(
    factory: OperatorFactory, email: str, password: str
) -> None:
    with pytest.raises(ValidationError):
        await factory.operator(email, password=password)
