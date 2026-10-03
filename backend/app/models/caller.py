from pydantic import BaseModel


class Caller(BaseModel):
    """What the checks need to know about the user of one request, resolved from the token."""

    role: str  # the user's roles joined for display: shown in reasons and sent to Jev as context
    allowed_tools: list[str]
