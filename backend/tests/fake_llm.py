import json
from collections import deque
from collections.abc import Sequence
from typing import Final

from app.llm.provider import ChatMessage, LLMResponse, ResponseSchema

FAKE_EXPLANATION: Final = "Fake explanation."
FAKE_MODEL: Final = "fake-llm"
TEST_EXPLAIN_LIMIT: Final = 10


class FakeLLM:
    def __init__(self, *replies: str | Exception) -> None:
        self._replies: deque[str | Exception] = deque(replies)
        self.calls: list[list[ChatMessage]] = []
        self.schemas: list[ResponseSchema] = []

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.schemas.append(schema)
        reply = self._replies.popleft() if self._replies else json.dumps({"text": FAKE_EXPLANATION})
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model=FAKE_MODEL, input_tokens=0, output_tokens=0)
