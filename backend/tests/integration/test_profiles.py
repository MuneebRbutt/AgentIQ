"""Business-profile API against a real database (FR-08-04/05/06, FR-09-02, TC-08)."""

import copy
import uuid
from typing import Any

import httpx
import pytest

from app.profiles.examples import _raw_example
from app.profiles.schema import Domain
from app.tenancy.models import Tenant
from tests.integration.conftest import DEFAULT_PASSWORD, OperatorFactory

pytestmark = pytest.mark.integration

PROFILES = "/api/v1/profiles"


async def _login(
    client: httpx.AsyncClient, factory: OperatorFactory, email: str, tenant: Tenant
) -> dict[str, str]:
    await factory.operator(email, tenant=tenant)
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def headers(live_client: httpx.AsyncClient, factory: OperatorFactory) -> dict[str, str]:
    return await _login(live_client, factory, "owner@hotel.example.com", await factory.tenant())


def _example(domain: Domain = Domain.RESTAURANT) -> dict[str, Any]:
    return copy.deepcopy(_raw_example(domain))


async def _create(
    client: httpx.AsyncClient, headers: dict[str, str], config: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.post(PROFILES, json=config or _example(), headers=headers)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_tc08_create_profile_with_all_mandatory_fields(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(live_client, headers)

    assert created["name"] == "Spice Garden"
    assert created["domain"] == "restaurant"
    assert created["version"] == 1

    fetched = await live_client.get(f"{PROFILES}/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["config"] == created["config"]
    assert fetched.json()["config"]["booking_rules"]["slot_minutes"] == 30


async def test_tc08_missing_mandatory_field_blocks_the_save(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    config = _example()
    del config["domain"]

    response = await live_client.post(PROFILES, json=config, headers=headers)

    assert response.status_code == 422
    assert ["body", "domain"] in [error["loc"] for error in response.json()["errors"]]
    assert (await live_client.get(PROFILES, headers=headers)).json() == []


async def test_validate_reports_problems_without_saving(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    config = _example()
    config["domain"] = ""
    config["booking_rules"]["max_party_size"] = 0

    report = (await live_client.post(f"{PROFILES}/validate", json=config, headers=headers)).json()
    ok = (await live_client.post(f"{PROFILES}/validate", json=_example(), headers=headers)).json()

    assert report["valid"] is False
    assert {tuple(error["loc"]) for error in report["errors"]} == {
        ("domain",),
        ("booking_rules", "max_party_size"),
    }
    assert ok == {"valid": True, "errors": []}
    assert (await live_client.get(PROFILES, headers=headers)).json() == []


async def test_edit_increments_the_version_and_keeps_history(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(live_client, headers)
    config = created["config"]
    config["booking_rules"]["cancellation_notice_hours"] = 6

    response = await live_client.put(
        f"{PROFILES}/{created['id']}", json={"version": 1, "config": config}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["config"]["booking_rules"]["cancellation_notice_hours"] == 6

    versions = (
        await live_client.get(f"{PROFILES}/{created['id']}/versions", headers=headers)
    ).json()
    assert [entry["version"] for entry in versions] == [2, 1]
    assert all(entry["saved_by"] is not None for entry in versions)


async def test_stale_edit_is_refused(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(live_client, headers)
    url = f"{PROFILES}/{created['id']}"
    first = await live_client.put(
        url, json={"version": 1, "config": created["config"]}, headers=headers
    )
    assert first.status_code == 200

    # A second operator still looking at version 1 tries to save.
    stale = await live_client.put(
        url, json={"version": 1, "config": created["config"]}, headers=headers
    )

    assert stale.status_code == 409
    assert stale.json()["current_version"] == 2


async def test_switching_domain_is_a_configuration_change(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(live_client, headers, _example(Domain.HOTEL))

    response = await live_client.put(
        f"{PROFILES}/{created['id']}",
        json={"version": 1, "config": _example(Domain.RESTAURANT)},
        headers=headers,
    )

    # UC-08 alternative path: hotel -> restaurant without code changes.
    assert response.status_code == 200
    assert response.json()["domain"] == "restaurant"
    assert response.json()["config"]["booking_rules"]["mode"] == "time_slot"


async def test_invalid_edit_leaves_the_profile_unchanged(
    live_client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    created = await _create(live_client, headers)
    config = created["config"]
    config["services"] = []

    response = await live_client.put(
        f"{PROFILES}/{created['id']}", json={"version": 1, "config": config}, headers=headers
    )

    assert response.status_code == 422
    assert (await live_client.get(f"{PROFILES}/{created['id']}", headers=headers)).json()[
        "version"
    ] == 1


async def test_list_and_delete(live_client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    restaurant = await _create(live_client, headers)
    await _create(live_client, headers, _example(Domain.CLINIC))

    listed = (await live_client.get(PROFILES, headers=headers)).json()
    assert [item["name"] for item in listed] == ["CareWell Family Clinic", "Spice Garden"]
    assert "config" not in listed[0]

    deleted = await live_client.delete(f"{PROFILES}/{restaurant['id']}", headers=headers)
    assert deleted.status_code == 204
    gone = await live_client.get(f"{PROFILES}/{restaurant['id']}", headers=headers)
    assert gone.status_code == 404
    assert len((await live_client.get(PROFILES, headers=headers)).json()) == 1


async def test_templates(live_client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    listed = (await live_client.get(f"{PROFILES}/templates", headers=headers)).json()
    clinic = await live_client.get(f"{PROFILES}/templates/clinic", headers=headers)
    unknown = await live_client.get(f"{PROFILES}/templates/spa", headers=headers)

    assert {item["domain"] for item in listed} == {"hotel", "restaurant", "clinic"}
    assert clinic.status_code == 200
    assert clinic.json()["booking_rules"]["max_party_size"] == 1
    assert unknown.status_code == 422
    # A template can be saved as-is.
    await _create(live_client, headers, clinic.json())


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", PROFILES),
        ("POST", PROFILES),
        ("GET", f"{PROFILES}/templates"),
        ("POST", f"{PROFILES}/validate"),
        ("GET", f"{PROFILES}/{uuid.uuid4()}"),
    ],
)
async def test_profiles_need_login(live_client: httpx.AsyncClient, method: str, path: str) -> None:
    response = await live_client.request(method, path, json={})

    assert response.status_code == 401


async def test_other_businesses_profiles_are_invisible(
    live_client: httpx.AsyncClient, factory: OperatorFactory, headers: dict[str, str]
) -> None:
    clinic_headers = await _login(
        live_client, factory, "owner@clinic.example.com", await factory.tenant("City Clinic")
    )
    hotel_profile = await _create(live_client, headers, _example(Domain.HOTEL))
    url = f"{PROFILES}/{hotel_profile['id']}"

    # NFR03-02: every route answers as if the profile didn't exist.
    assert (await live_client.get(PROFILES, headers=clinic_headers)).json() == []
    for response in (
        await live_client.get(url, headers=clinic_headers),
        await live_client.get(f"{url}/versions", headers=clinic_headers),
        await live_client.put(
            url, json={"version": 1, "config": _example(Domain.HOTEL)}, headers=clinic_headers
        ),
        await live_client.delete(url, headers=clinic_headers),
    ):
        assert response.status_code == 404, response.text

    assert (await live_client.get(url, headers=headers)).json()["version"] == 1
