from typing import Any

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    # Parsed from the OpenAI JSON string by the adapter
    arguments: dict[str, Any] = Field(default_factory=dict)
