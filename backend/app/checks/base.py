"""Helpers shared by the checks: result building, text selection and safe labels.

The interfaces themselves live in app.protocols; they are re-exported here for the checks.
"""

from __future__ import annotations

import json
import re
import time

from app.models import (
    Action,
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    Redaction,
    Verdict,
)
from app.protocols.check import Check, CheckContext, Signature
from app.protocols.judge import Judge, JudgeUnavailable

__all__ = [
    "REPLY_INDEX",
    "Check",
    "CheckContext",
    "Judge",
    "JudgeUnavailable",
    "Signature",
    "make_result",
    "safe_label",
    "targets",
]

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


_VERDICT_ACTION = {"allow": Action.ALLOW, "redact": Action.REDACT, "block": Action.BLOCK}


def make_result(
    check_id: str,
    request: CanonicalRequest,
    verdict: Verdict,
    reason: str,
    started: float,
    *,
    score: float | None = None,
    redactions: list[Redaction] | None = None,
    decided_by: DecidedBy = DecidedBy.RULES,
) -> CheckResult:
    """Build a CheckResult. `action` is provisional; the pipeline overwrites it from the mode."""
    if score is None:
        score = 0.0 if verdict == "allow" else 1.0
    return CheckResult(
        check=check_id,
        checkpoint=request.checkpoint,
        verdict=verdict,
        action=_VERDICT_ACTION.get(verdict, Action.BLOCK),
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
    if cp == Checkpoint.OUTPUT:
        text = request.reply.content if request.reply else None
        return [(REPLY_INDEX, text)] if text else []
    if cp == Checkpoint.TOOL_CALL:
        calls = request.reply.tool_calls if request.reply else []
        if not calls:
            return []
        payload = [{"name": c.name, "arguments": c.arguments} for c in calls]
        return [(REPLY_INDEX, json.dumps(payload, ensure_ascii=False))]
    if scope == "all":
        return [(i, m.content) for i, m in enumerate(msgs) if m.content]
    role = "tool" if cp == Checkpoint.TOOL_RESULT else "user"
    indexed = [(i, m.content) for i, m in enumerate(msgs) if m.role == role and m.content]
    if cp == Checkpoint.INPUT:
        return indexed[-1:]
    trailing: list[tuple[int, str]] = []
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role != "tool":
            break
        if msgs[i].content:
            trailing.append((i, msgs[i].content))
    return list(reversed(trailing))
