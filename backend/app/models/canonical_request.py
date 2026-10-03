from pydantic import BaseModel, Field

from app.models.enums import Checkpoint
from app.models.message import Message
from app.models.tool_def import ToolDef


class CanonicalRequest(BaseModel):
    """The internal format every check sees. Vendor JSON never gets past the adapter."""

    request_id: str
    caller_id: str
    model: str
    checkpoint: Checkpoint
    messages: list[Message]
    tools: list[ToolDef] = Field(default_factory=list)
    reply: Message | None = None  # set only at tool_call / output
