from datetime import datetime

from pydantic import BaseModel


class PolicyDocument(BaseModel):
    """GET /api/policy: the policy in force, as the file text."""

    yaml: str
    version: str
    loaded_at: datetime
