from typing import Literal

from pydantic import BaseModel, Field

from app.models.tool_call import ToolCall


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only
