from typing import Any

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    # Parsed from the OpenAI JSON string by the adapter
    arguments: dict[str, Any]
