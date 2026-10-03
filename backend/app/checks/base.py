"""The one interface every check implements (constitution III). One check, one module."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.budget import UsageLedger
from app.models import (
    Action,
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    JudgeInput,
    JudgeVerdict,
    Redaction,
    Verdict,
)

# message_index used for the upstream reply (contracts/models.md, Redaction)
REPLY_INDEX = -1

_SAFE_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}")


def safe_label(name: object) -> str:
    """A model, tool or argument name as it may appear in a reason.

    These names are chosen by the model or the caller, and reasons go to the audit log, the
    exports and the refusal text. Anything that is not a plain identifier is withheld, so a
    secret or PII smuggled into a name cannot leak that way.
    """
    text = name if isinstance(name, str) else ""
    return text if _SAFE_LABEL.fullmatch(text) else "<unprintable name>"


class JudgeUnavailable(Exception):
    """Neither Jev nor the fallback produced a verdict. The pipeline fails closed."""


class Judge(Protocol):
    async def judge(self, inp: JudgeInput) -> JudgeVerdict:
        """Raise JudgeUnavailable when no decision maker answers."""
        ...


@dataclass(frozen=True)
class Signature:
    id: str
    pattern: re.Pattern[str]
    category: str = "other"
    description: str = ""


@dataclass
class CheckContext:
    """Everything a check may use besides the request and its own policy section.

    Checks never import other checks or the policy store; the pipeline fills this in.
    """

    caller_role: str = ""
    allowed_models: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    requests_per_minute: int | None = None
    tokens_per_day: int | None = None
    cost_per_day: float | None = None
    ledger: UsageLedger | None = None
    # None means the feed never loaded: the signatures check must report an error, not allow.
    signatures: list[Signature] | None = None
    jev_threshold: float = 1.0
    judge: Judge | None = None


class Check(Protocol):
    id: str
    cost_rank: int
    checkpoints: frozenset[Checkpoint]

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        """Return the raw verdict (allow | redact | block | error); the pipeline maps the mode."""
        ...


_VERDICT_ACTION = {"allow": Action.allow, "redact": Action.redact, "block": Action.block}


def make_result(
    check_id: str,
    request: CanonicalRequest,
    verdict: Verdict,
    reason: str,
    started: float,
    *,
    score: float | None = None,
    redactions: list[Redaction] | None = None,
    decided_by: DecidedBy = DecidedBy.rules,
) -> CheckResult:
    """Build a CheckResult. `action` is provisional; the pipeline overwrites it from the mode."""
    if score is None:
        score = 0.0 if verdict == "allow" else 1.0
    return CheckResult(
        check=check_id,
        checkpoint=request.checkpoint,
        verdict=verdict,
        action=_VERDICT_ACTION.get(verdict, Action.block),
        score=score,
        reason=reason,
        redactions=redactions or [],
        latency_ms=(time.perf_counter() - started) * 1000.0,
        decided_by=decided_by,
    )


def targets(request: CanonicalRequest, *, scope: str = "new") -> list[tuple[int, str]]:
    """Texts a check inspects at the request's checkpoint, as (message_index, text).

    scope="new": only what this step added (last user message, trailing tool results).
    scope="all": every message with content, of every role (system, user, assistant, tool).
    The whole conversation is forwarded upstream and the agent re-sends it raw on every step,
    so content blocked or redacted on an earlier step comes back and must be caught again.
    At tool_call and output the scope does not matter: the text is the reply (REPLY_INDEX), and
    at tool_call it is the JSON of all tool calls.
    """
    cp = request.checkpoint
    msgs = request.messages
    if cp == Checkpoint.output:
        text = request.reply.content if request.reply else None
        return [(REPLY_INDEX, text)] if text else []
    if cp == Checkpoint.tool_call:
        calls = request.reply.tool_calls if request.reply else []
        if not calls:
            return []
        payload = [{"name": c.name, "arguments": c.arguments} for c in calls]
        return [(REPLY_INDEX, json.dumps(payload, ensure_ascii=False))]
    if scope == "all":
        return [(i, m.content) for i, m in enumerate(msgs) if m.content]
    role = "tool" if cp == Checkpoint.tool_result else "user"
    indexed = [(i, m.content) for i, m in enumerate(msgs) if m.role == role and m.content]
    if cp == Checkpoint.input:
        return indexed[-1:]
    trailing: list[tuple[int, str]] = []
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role != "tool":
            break
        if msgs[i].content:
            trailing.append((i, msgs[i].content))
    return list(reversed(trailing))
