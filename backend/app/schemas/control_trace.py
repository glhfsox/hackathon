from pydantic import BaseModel, Field

from app.models import Decision


class ControlTrace(BaseModel):
    """The decision trace attached to every response as the `control` field."""

    request_id: str
    decisions: list[Decision] = Field(default_factory=list)
