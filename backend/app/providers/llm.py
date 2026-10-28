"""Language-model adapter (CLAUDE.md rule 4; Q-1 pending, ADR-0004).

Any provider with an OpenAI-compatible chat-completions endpoint works (OpenAI, xAI Grok,
Groq, Together, a local Ollama or vLLM server) by setting `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY`. Tests use a fake that implements `LLMClient`; they never call a paid API.

Cost note: one knowledge answer is one call of roughly 1-2k input tokens and up to
`max_tokens` output tokens.
"""

import re
from http import HTTPStatus
from typing import Any, Literal, Protocol, TypedDict

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMError(Exception):
    """The model couldn't be reached or returned something unusable. Callers fall back."""


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        json_mode: bool = False,
    ) -> str: ...

    async def aclose(self) -> None: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._json_mode_supported = True
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and self._json_mode_supported:
            # The model must answer with one JSON object (structured output).
            payload["response_format"] = {"type": "json_object"}
        try:
            response = await self._http.post("/chat/completions", json=payload)
            if response.status_code == HTTPStatus.BAD_REQUEST and "response_format" in payload:
                # Some providers or models refuse JSON mode; the prompt asks for JSON anyway.
                log.info("llm_json_mode_unsupported", model=self._model)
                self._json_mode_supported = False
                del payload["response_format"]
                response = await self._http.post("/chat/completions", json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            # The body may echo the prompt; log only the status (NFR03 logging rules).
            log.warning("llm_http_error", status=exc.response.status_code, model=self._model)
            raise LLMError(f"model returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            log.warning("llm_unreachable", error=type(exc).__name__, model=self._model)
            raise LLMError(f"model unreachable: {type(exc).__name__}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError("model returned an unexpected response") from exc
        if not isinstance(content, str):
            raise LLMError("model returned an empty answer")
        # Reasoning models on some providers put their working in <think> tags.
        content = _THINKING.sub("", content).strip()
        if not content:
            raise LLMError("model returned an empty answer")
        return content

    async def aclose(self) -> None:
        await self._http.aclose()


def create_llm(settings: Settings) -> LLMClient | None:
    """The configured model, or None when no API key is set (features degrade, NFR04-01)."""
    if settings.llm_api_key is None:
        return None
    return OpenAICompatibleClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
