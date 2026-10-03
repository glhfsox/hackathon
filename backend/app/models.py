"""Shared shapes. Mirrors contracts/models.md exactly; change the contract first."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Checkpoint(StrEnum):
    input = "input"
    tool_call = "tool_call"
    tool_result = "tool_result"
    output = "output"


class Action(StrEnum):
    allow = "allow"
    redact = "redact"
    block = "block"
    flag = "flag"


class Mode(StrEnum):
    off = "off"
    monitor = "monitor"
    redact = "redact"
    block = "block"


class DecidedBy(StrEnum):
    rules = "rules"
    jev = "jev"
    fallback = "fallback"


Verdict = Literal["allow", "redact", "block", "error"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDef(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class CanonicalRequest(BaseModel):
    request_id: str
    caller_id: str
    model: str
    checkpoint: Checkpoint
    messages: list[Message]
    tools: list[ToolDef] = Field(default_factory=list)
    reply: Message | None = None


class Redaction(BaseModel):
    kind: str
    start: int
    end: int
    replacement: str
    message_index: int


class CheckResult(BaseModel):
    check: str
    checkpoint: Checkpoint
    verdict: Verdict
    action: Action
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    redactions: list[Redaction] = Field(default_factory=list)
    latency_ms: float
    decided_by: DecidedBy = DecidedBy.rules


class Decision(BaseModel):
    request_id: str
    checkpoint: Checkpoint
    action: Action
    blocked_by: str | None = None
    results: list[CheckResult] = Field(default_factory=list)


class JudgeInput(BaseModel):
    checkpoint: Checkpoint
    text: str
    context: str = ""


class JudgeVerdict(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    categories: list[str] = Field(default_factory=list)
    decided_by: Literal["jev", "fallback"]


class Usage(BaseModel):
    """Upstream usage of one model call, handed to the pipeline at the reply checkpoints."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    upstream_latency_ms: float | None = Field(default=None, ge=0.0)


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
    decided_by: DecidedBy = DecidedBy.rules
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
