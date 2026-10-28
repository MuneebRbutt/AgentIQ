import re

import httpx
import pytest

from app.core.middleware import REQUEST_ID_HEADER


async def test_generates_request_id_when_missing(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


async def test_echoes_valid_incoming_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: "abc-123.x_y"})

    assert response.headers[REQUEST_ID_HEADER] == "abc-123.x_y"


@pytest.mark.parametrize("bad", ["has space", "a" * 65, "line\nbreak", "<script>"])
async def test_replaces_unsafe_incoming_request_id(client: httpx.AsyncClient, bad: str) -> None:
    response = await client.get("/api/v1/health/live", headers={REQUEST_ID_HEADER: bad})

    assert response.headers[REQUEST_ID_HEADER] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])
