from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    """Body of POST /v1/chat/completions in the OpenAI format.

    Messages and tools stay as raw dicts: the OpenAI adapter is the only place that parses
    them into canonical models. Unknown OpenAI parameters are kept so they reach the upstream.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False  # accepted, but always answered non-streamed
