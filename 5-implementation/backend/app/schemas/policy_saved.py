from datetime import datetime

from pydantic import BaseModel


class PolicySaved(BaseModel):
    """PUT /api/policy 200: the version now in force."""

    version: str
    loaded_at: datetime
