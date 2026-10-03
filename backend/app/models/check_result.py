from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import Action, Checkpoint, DecidedBy
from app.models.redaction import Redaction


class CheckResult(BaseModel):
    check: str
    checkpoint: Checkpoint
    verdict: Literal["allow", "redact", "block", "error"]  # raw opinion, before the mode
    action: Action  # final action after the mode mapping
    score: float = Field(ge=0, le=1)
    reason: str
    redactions: list[Redaction] = Field(default_factory=list)
    latency_ms: float
    decided_by: DecidedBy = DecidedBy.RULES
