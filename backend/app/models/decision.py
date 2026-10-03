from pydantic import BaseModel, Field

from app.models.check_result import CheckResult
from app.models.enums import Action, Checkpoint


class Decision(BaseModel):
    """Pipeline result for one checkpoint."""

    request_id: str
    checkpoint: Checkpoint
    action: Action  # strongest result: block > redact > flag > allow
    blocked_by: str | None = None  # check id
    results: list[CheckResult] = Field(default_factory=list)
