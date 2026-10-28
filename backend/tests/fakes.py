"""Test doubles shared by unit and integration tests. No test calls a paid API."""

import json
from dataclasses import dataclass, field
from typing import Any

from app.providers.llm import ChatMessage


@dataclass
class FakeLLM:
    """A scripted model.

    Plain requests (answers, replies) get `reply`. JSON requests (the orchestrator's
    understand step) get the next item of `understandings`; dicts are sent as JSON and
    exceptions are raised.
    """

    reply: str | Exception = "OK"
    understandings: list[dict[str, Any] | str | Exception] = field(default_factory=list)
    calls: list[list[ChatMessage]] = field(default_factory=list)
    json_calls: list[list[ChatMessage]] = field(default_factory=list)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 400,
        json_mode: bool = False,
    ) -> str:
        if json_mode:
            self.json_calls.append(messages)
            if not self.understandings:
                raise AssertionError("the test didn't script an understanding for this turn")
            item = self.understandings.pop(0)
        else:
            self.calls.append(messages)
            item = self.reply
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    async def aclose(self) -> None:
        return None
