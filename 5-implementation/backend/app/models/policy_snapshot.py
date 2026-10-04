from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app.models.policy import Policy


class PolicySnapshot(BaseModel):
    """One validated policy as loaded. A request takes one snapshot and uses it to the end."""

    model_config = {"frozen": True}

    policy: Policy
    version: str  # "<policy.version>+<content hash>": two different files never share one
    yaml: str
    loaded_at: datetime
    path: Path | None = None
