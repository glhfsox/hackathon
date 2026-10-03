from pydantic import BaseModel

from app.models.enums import Action, Checkpoint, DecidedBy

# AuditRecord.check of the one summary row the pipeline writes per checkpoint.
TURN_SUMMARY = "turn_summary"


class AuditRecord(BaseModel):
    id: int | None = None
    ts: str
    request_id: str | None = None
    caller_id: str | None = None
    model: str | None = None
    checkpoint: Checkpoint | None = None
    check: str
    action: Action
    reason: str
    score: float = 0.0
    latency_ms: float = 0.0
    decided_by: DecidedBy = DecidedBy.RULES
    tokens: int = 0
    cost: float = 0.0
    policy_version: str
    # Agent and economic facts. Set on `turn_summary` rows only; null everywhere else.
    conversation_id: str | None = None
    step: int | None = None
    messages: int | None = None
    tool_calls: int | None = None
    tools: str | None = None
    tool_calls_total: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    upstream_latency_ms: float | None = None
    overhead_ms: float | None = None
    blocked_by: str | None = None
