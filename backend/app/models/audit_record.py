from pydantic import BaseModel

from app.models.enums import Action, Checkpoint, DecidedBy


class AuditRecord(BaseModel):
    """One row per CheckResult, plus rows for system events. Append-only."""

    id: int
    ts: str
    request_id: str | None = None
    caller_id: str | None = None
    model: str | None = None
    checkpoint: Checkpoint | None = None
    check: str  # check id, or a system event such as auth_failed or policy_rejected
    action: Action
    reason: str
    score: float
    latency_ms: float
    decided_by: DecidedBy
    tokens: int = 0
    cost: float = 0.0
    policy_version: str
