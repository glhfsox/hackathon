from pydantic import BaseModel, Field


class Caller(BaseModel):
    """What the checks need to know about the user of one request, resolved from the token."""

    role: str  # the user's roles joined for display: shown in reasons and sent to Jev as context
    roles: list[str] = Field(default_factory=list)  # the token's roles: budget picks limits by them
    allowed_tools: list[str]
