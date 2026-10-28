"""One business can never see another business's data (NFR03-02)."""

import uuid

import httpx
import pytest

from tests.integration.conftest import DEFAULT_PASSWORD, OperatorFactory

pytestmark = pytest.mark.integration


async def _token(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_operator_list_only_shows_own_business(
    live_client: httpx.AsyncClient, factory: OperatorFactory
) -> None:
    hotel = await factory.tenant("Grand Hotel")
    clinic = await factory.tenant("City Clinic")
    await factory.operator("owner@hotel.example.com", tenant=hotel)
    await factory.operator("staff@hotel.example.com", tenant=hotel)
    await factory.operator("owner@clinic.example.com", tenant=clinic)

    response = await live_client.get(
        "/api/v1/operators", headers=await _token(live_client, "owner@hotel.example.com")
    )

    assert response.status_code == 200
    emails = {operator["email"] for operator in response.json()}
    assert emails == {"owner@hotel.example.com", "staff@hotel.example.com"}
    assert {operator["tenant_id"] for operator in response.json()} == {str(hotel.id)}


async def test_other_business_records_look_like_they_do_not_exist(
    live_client: httpx.AsyncClient, factory: OperatorFactory
) -> None:
    hotel_owner = await factory.operator(
        "owner@hotel.example.com", tenant=await factory.tenant("Grand Hotel")
    )
    clinic_owner = await factory.operator(
        "owner@clinic.example.com", tenant=await factory.tenant("City Clinic")
    )
    headers = await _token(live_client, "owner@hotel.example.com")

    own = await live_client.get(f"/api/v1/operators/{hotel_owner.id}", headers=headers)
    other = await live_client.get(f"/api/v1/operators/{clinic_owner.id}", headers=headers)
    missing = await live_client.get(f"/api/v1/operators/{uuid.uuid4()}", headers=headers)

    assert own.status_code == 200
    # Same answer for "someone else's" and "doesn't exist", so IDs can't be probed.
    assert other.status_code == missing.status_code == 404
    assert other.json()["detail"] == missing.json()["detail"]
