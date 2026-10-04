from typing import Literal

from pydantic import BaseModel, Field


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0, le=1)  # risk; at or above the policy threshold means block
    reason: str
    categories: list[str] = Field(default_factory=list)  # e.g. prompt_injection
    decided_by: Literal["jev", "fallback"]
