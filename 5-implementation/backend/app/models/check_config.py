from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import Mode


class CheckConfig(BaseModel):
    """How one check is configured at one checkpoint. The policy will supply it."""

    mode: Mode
    settings: dict[str, Any] = Field(default_factory=dict)  # the check's own policy section
