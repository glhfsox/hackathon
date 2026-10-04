from typing import Any

from pydantic import BaseModel, Field


class ToolDef(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON Schema
