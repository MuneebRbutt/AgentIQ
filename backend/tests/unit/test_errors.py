import httpx
import pytest
from fastapi import FastAPI

from app.core.errors import PROBLEM_JSON, ConflictError, NotFoundError


@pytest.fixture
def app_with_failing_routes(app: FastAPI) -> FastAPI:
    @app.get("/_test/not-found")
    async def not_found() -> None:
        raise NotFoundError("Booking not found.")

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise ConflictError("Slot already taken.", extra={"alternatives": ["19:30"]})

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_test/typed")
    async def typed(count: int) -> dict[str, int]:
        return {"count": count}

    return app


async def test_app_error_becomes_problem_response(
    app_with_failing_routes: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.get("/_test/not-found")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["code"] == "not_found"
    assert body["detail"] == "Booking not found."
    assert body["instance"] == "/_test/not-found"
    assert body["request_id"] == response.headers["X-Request-ID"]


async def test_app_error_extra_fields_are_included(
    app_with_failing_routes: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.get("/_test/conflict")

    assert response.status_code == 409
    assert response.json()["alternatives"] == ["19:30"]


async def test_unexpected_error_hides_internals(
    app_with_failing_routes: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret internal detail" not in response.text


async def test_validation_error_lists_fields(
    app_with_failing_routes: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.get("/_test/typed", params={"count": "many"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["loc"] == ["query", "count"]


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "not_found"
