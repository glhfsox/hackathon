from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    policy_version: str | None = None  # None until a policy has been loaded
    jev: Literal["up", "down"]
    fallback: Literal["up", "down"]
