from typing import Any

from pydantic import BaseModel, ConfigDict

from app.schemas.control_trace import ControlTrace


class ChatCompletionResponse(BaseModel):
    """OpenAI chat completion plus the `control` trace. Normal clients ignore `control`.

    Used both for upstream replies and for refusals, which are HTTP 200 in the same shape.
    Unknown upstream fields are kept.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    object: str = "chat.completion"
    model: str
    choices: list[dict[str, Any]]
    usage: dict[str, int] | None = None
    control: ControlTrace
