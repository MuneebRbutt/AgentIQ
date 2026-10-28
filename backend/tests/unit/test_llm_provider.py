"""The OpenAI-compatible LLM adapter, against a mocked HTTP transport (no real calls)."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.providers.llm import LLMError, OpenAICompatibleClient, create_llm


def _client(handler: httpx.MockTransport) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://llm.example.com/v1/",
        api_key="test-key",
        model="test-model",
        timeout_seconds=5,
        transport=handler,
    )


def _reply(content: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_sends_a_chat_completion_and_returns_the_text() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _reply("  Check-in is at 2 PM.  ")

    client = _client(httpx.MockTransport(handle))
    answer = await client.complete([{"role": "user", "content": "When is check-in?"}])
    await client.aclose()

    assert answer == "Check-in is at 2 PM."
    [request] = seen
    assert str(request.url) == "https://llm.example.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.0
    assert body["messages"] == [{"role": "user", "content": "When is check-in?"}]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="upstream exploded"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, text="not json"),
        _reply(""),
        _reply(None),
    ],
)
async def test_bad_responses_raise_llm_error(response: httpx.Response) -> None:
    client = _client(httpx.MockTransport(lambda request: response))
    with pytest.raises(LLMError):
        await client.complete([{"role": "user", "content": "hi"}])


async def test_network_failure_raises_llm_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    client = _client(httpx.MockTransport(fail))
    with pytest.raises(LLMError, match="unreachable"):
        await client.complete([{"role": "user", "content": "hi"}])


async def test_no_key_means_no_model() -> None:
    assert create_llm(Settings(llm_api_key=None)) is None
    assert create_llm(Settings(llm_api_key="   ")) is None
    llm = create_llm(Settings(llm_api_key="sk-test"))
    assert isinstance(llm, OpenAICompatibleClient)
    await llm.aclose()


async def test_json_mode_is_dropped_when_the_provider_refuses_it() -> None:
    bodies: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format not supported"})
        return _reply('{"ok": true}')

    client = _client(httpx.MockTransport(handle))
    assert await client.complete([{"role": "user", "content": "x"}], json_mode=True) == (
        '{"ok": true}'
    )
    # Remembered: the next JSON request doesn't try again.
    await client.complete([{"role": "user", "content": "x"}], json_mode=True)
    assert ["response_format" in body for body in bodies] == [True, False, False]


async def test_reasoning_is_stripped_from_the_answer() -> None:
    client = _client(httpx.MockTransport(lambda r: _reply("<think>\nhmm\n</think>\n\nready")))
    assert await client.complete([{"role": "user", "content": "x"}]) == "ready"
    only_thinking = _client(httpx.MockTransport(lambda r: _reply("<think>hmm</think>")))
    with pytest.raises(LLMError, match="empty"):
        await only_thinking.complete([{"role": "user", "content": "x"}])


async def test_check_llm_command(capsys: pytest.CaptureFixture[str]) -> None:
    from app import cli
    from tests.fakes import FakeLLM

    settings = Settings(_env_file=None, llm_api_key="k", llm_model="grok-test")
    assert await cli.check_llm(settings, FakeLLM(reply="ready", understandings=[{"ok": True}]))
    assert "The model works." in capsys.readouterr().out
    assert not await cli.check_llm(settings, FakeLLM(reply=LLMError("HTTP 401")))
    assert "failed: HTTP 401" in capsys.readouterr().out
    assert not await cli.check_llm(Settings(_env_file=None))
