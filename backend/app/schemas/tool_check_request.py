from pydantic import BaseModel, Field

from app.models import Message, ToolCall


class ToolCheckRequest(BaseModel):
    """Body of POST /v1/tools/check: one tool call and the conversation it belongs to."""

    tool_call: ToolCall
    messages: list[Message] = Field(default_factory=list)
