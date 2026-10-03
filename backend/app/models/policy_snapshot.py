from datetime import datetime

from pydantic import BaseModel


class PolicySnapshot(BaseModel):
    """The active policy as loaded. Placeholder until the policy schema is decided.

    A request takes one snapshot at its start and uses it to the end.
    """

    version: str
    yaml: str
    loaded_at: datetime
