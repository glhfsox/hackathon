"""Check pipeline: runs the checks for one checkpoint and maps verdicts to actions.

docs/architecture.md §3 (checkpoint detection) and §4 (mode table, cost order, first block stops).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.checks import ordered_checks
from app.checks.base import (
    REPLY_INDEX,
    Check,
    CheckContext,
    Judge,
    Signature,
    make_result,
    tool_call_view,
)
from app.core.budget import UsageLedger
from app.models import (
    TURN_SUMMARY,
    Action,
    AuditRecord,
    Caller,
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    Decision,
    Message,
    PolicySnapshot,
    Redaction,
    Usage,
    Verdict,
)
from app.models.policy import Policy
from app.protocols.audit import AuditSink

logger = logging.getLogger(__name__)

# Used when a check's policy section sets no timeout_s.
DEFAULT_TIMEOUT_S = 10.0

_STRENGTH = {Action.ALLOW: 0, Action.FLAG: 1, Action.REDACT: 2, Action.BLOCK: 3}
# Usage is only known once the upstream has answered, i.e. on the reply checkpoints.
_USAGE_CHECKPOINTS = {Checkpoint.TOOL_CALL, Checkpoint.OUTPUT}
_PAST = {Action.BLOCK: "blocked", Action.REDACT: "redacted", Action.FLAG: "flagged"}
# Hex digits of the conversation hash: 64 bits, so distinct sessions practically never collide.
_CONVERSATION_ID_LEN = 16


def conversation_id(caller_id: str, messages: list[Message]) -> str:
    """A stable id for one agent session, from the caller and its first user message.

    The agent re-sends the whole conversation on every step, so every step of a session yields
    the same id without any help from the client. Limitation: two sessions of one caller that
    start with the same first user message get the same id and are counted as one session.
    """
    first = next((m.content or "" for m in messages if m.role == "user"), "")
    digest = hashlib.sha256(f"{caller_id}\0{first}".encode()).hexdigest()
    return digest[:_CONVERSATION_ID_LEN]


def detect_request_checkpoint(messages: list[Message]) -> Checkpoint:
    if messages and messages[-1].role == "tool":
        return Checkpoint.TOOL_RESULT
    return Checkpoint.INPUT


def detect_reply_checkpoint(reply: Message) -> Checkpoint:
    return Checkpoint.TOOL_CALL if reply.tool_calls else Checkpoint.OUTPUT


def to_action(verdict: Verdict) -> Action:
    """A check's verdict as the pipeline's action (docs/architecture.md §4). There are no modes:
    a finding blocks, a redaction is applied, and an error fails closed."""
    if verdict == "allow":
        return Action.ALLOW
    return Action.REDACT if verdict == "redact" else Action.BLOCK


def apply_redactions(request: CanonicalRequest, redactions: list[Redaction]) -> CanonicalRequest:
    """Return a deep copy with the redactions applied. The original request is not modified.

    Offsets point into the original content, so spans are applied right to left. Overlapping
    spans are merged first: applying them one by one would cut into an earlier replacement or
    leave part of the secret behind.
    """
    out = request.model_copy(deep=True)
    by_index: dict[int, list[Redaction]] = {}
    for r in redactions:
        by_index.setdefault(r.message_index, []).append(r)
    for index, group in by_index.items():
        if index == REPLY_INDEX and out.checkpoint == Checkpoint.TOOL_CALL:
            # At tool_call the checks read the tool-call view: redact that copy, never the
            # arguments the agent will run.
            out.tool_call_view = _redact_text(tool_call_view(out), group)
            continue
        if index == REPLY_INDEX:
            target = out.reply
        elif 0 <= index < len(out.messages):
            target = out.messages[index]
        else:
            target = None
        if target is None or target.content is None:
            raise ValueError(f"redaction points at message {index}, which has no content")
        target.content = _redact_text(target.content, group)
    return out


def _redact_text(text: str, redactions: list[Redaction]) -> str:
    # Merge overlapping spans into (start, end, [replacements]) so no original byte survives.
    merged: list[tuple[int, int, list[str]]] = []
    for r in sorted(redactions, key=lambda r: (r.start, r.end)):
        if not 0 <= r.start <= r.end <= len(text):
            raise ValueError(f"redaction span {r.start}:{r.end} is outside a text of {len(text)}")
        if merged and r.start < merged[-1][1]:
            start, end, reps = merged[-1]
            if r.replacement not in reps:
                reps.append(r.replacement)
            merged[-1] = (start, max(end, r.end), reps)
        else:
            merged.append((r.start, r.end, [r.replacement]))
    for start, end, reps in reversed(merged):
        text = text[:start] + "".join(reps) + text[end:]
    return text


# A request without a caller may use nothing: no tool, no budget (fail closed).
NO_CALLER = Caller(role="none", allowed_tools=[])


def build_context(
    policy: Policy,
    caller: Caller = NO_CALLER,
    *,
    ledger: UsageLedger | None,
    signatures: list[Signature] | None,
    judge: Judge | None,
) -> CheckContext:
    return CheckContext(
        caller_role=caller.role,
        roles=list(caller.roles),
        allowed_tools=list(caller.allowed_tools),
        ledger=ledger,
        signatures=signatures,
        jev_threshold=policy.jev_threshold,
        judge=judge,
    )


async def _run_check(
    check: Check, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
) -> CheckResult:
    """Run one check; an exception or a timeout becomes verdict `error` (fail closed by mode)."""
    started = time.perf_counter()
    timeout_s = settings.get("timeout_s", DEFAULT_TIMEOUT_S)
    try:
        return await asyncio.wait_for(check.run(request, settings, ctx), timeout_s)
    except TimeoutError:
        logger.warning(
            "check %s timed out after %ss (request %s, checkpoint %s)",
            check.id,
            timeout_s,
            request.request_id,
            request.checkpoint,
        )
        reason = f"check timed out after {timeout_s}s"
    except Exception as exc:
        logger.exception(
            "check %s raised (request %s, checkpoint %s)",
            check.id,
            request.request_id,
            request.checkpoint,
        )
        reason = f"check error: {type(exc).__name__}: {exc}"
    return make_result(check.id, request, "error", reason, started)


async def run_checkpoint(
    request: CanonicalRequest,
    policy: Policy,
    policy_version: str,
    caller: Caller = NO_CALLER,
    *,
    ledger: UsageLedger | None = None,
    signatures: list[Signature] | None = None,
    judge: Judge | None = None,
    audit: AuditSink,
    checks: list[Check] | None = None,
    usage: Usage | None = None,
    usage_tokens: int = 0,
    usage_cost: float | None = None,
) -> tuple[Decision, CanonicalRequest]:
    """Run every enabled check for `request.checkpoint`, cheapest first.

    Returns the decision and the request to forward: it carries the redactions of checks whose
    action is `redact`. Each check sees the redactions of every check before it, so Jev only ever
    gets redacted text. A redaction's offsets point into the copy the checks saw, so every message
    a redaction touches is forwarded as the checks saw it.

    Writes one audit row per check result, then always one `turn_summary` row for the
    checkpoint (also when no check is enabled). Upstream usage goes on that row at the reply
    checkpoints only: `usage` if given (tokens = prompt + completion), else `usage_tokens`. Cost
    is `usage_cost`, or tokens / 1000 * the model's price_per_1k_tokens when it is None. Its
    conversation_id comes from `request.messages`: pass the agent's own (unredacted) messages at
    the reply checkpoint too, so every row of a session gets the same id.
    """
    checkpoint = request.checkpoint
    ctx = build_context(policy, caller, ledger=ledger, signatures=signatures, judge=judge)
    candidates = ordered_checks() if checks is None else checks
    results: list[CheckResult] = []
    blocked_by: str | None = None
    checked = request  # what the next check sees: every redaction so far
    forwarded = request  # what goes on: the messages a redaction touched, as the checks saw them
    if checkpoint == Checkpoint.TOOL_CALL:
        checked = request.model_copy(update={"tool_call_view": tool_call_view(request)})

    for check in sorted(candidates, key=lambda c: c.cost_rank):
        if checkpoint not in check.checkpoints:
            continue
        if not policy.enabled(check.id):
            continue
        # A copy, so a check cannot change the policy snapshot other requests share.
        settings = dict(policy.check_config(check.id).params)
        result = await _run_check(check, checked, settings, ctx)
        result.action = to_action(result.verdict)
        if checkpoint == Checkpoint.TOOL_CALL and result.action == Action.REDACT:
            # Only the tool-call view the later checks read is redacted; the agent gets its call
            # unchanged, so the audit must not claim a redaction.
            result.action = Action.ALLOW
            result.reason = f"{result.reason} in the copy the checks read; the call is unchanged"
        if result.redactions:
            try:
                checked = apply_redactions(checked, result.redactions)
                if result.action == Action.REDACT:
                    touched = {r.message_index for r in result.redactions}
                    forwarded = _copy_contents(forwarded, checked, touched)
            except ValueError as exc:
                # A bad span means the check misbehaved: fail closed like any other check error.
                logger.error(
                    "check %s returned an invalid redaction (request %s): %s",
                    check.id,
                    request.request_id,
                    exc,
                )
                result.verdict = "error"
                result.reason = f"check error: invalid redaction: {exc}"
                result.redactions = []
                result.action = to_action("error")
        results.append(result)
        await audit.write(_audit_record(request, result, policy_version))
        if result.action == Action.BLOCK:
            blocked_by = check.id
            break

    action = max((r.action for r in results), key=_STRENGTH.__getitem__, default=Action.ALLOW)
    decision = Decision(
        request_id=request.request_id,
        checkpoint=checkpoint,
        action=action,
        blocked_by=blocked_by,
        results=results,
    )
    await audit.write(
        _turn_summary(
            request,
            decision,
            policy,
            policy_version,
            usage=usage,
            tokens=usage_tokens,
            cost=usage_cost,
        )
    )
    return decision, forwarded


def _copy_contents(
    dst: CanonicalRequest, src: CanonicalRequest, indexes: set[int]
) -> CanonicalRequest:
    """A deep copy of `dst` whose messages at `indexes` (REPLY_INDEX: the reply) carry the
    content they have in `src`."""
    out = dst.model_copy(deep=True)
    for index in indexes:
        if index == REPLY_INDEX:
            if out.reply is not None and src.reply is not None:
                out.reply.content = src.reply.content
        else:
            out.messages[index].content = src.messages[index].content
    return out


def _audit_record(
    request: CanonicalRequest, result: CheckResult, policy_version: str
) -> AuditRecord:
    return AuditRecord(
        ts=datetime.now(UTC).isoformat(),
        request_id=request.request_id,
        caller_id=request.caller_id,
        model=request.model,
        checkpoint=request.checkpoint,
        check=result.check,
        action=result.action,
        reason=result.reason,
        score=result.score,
        latency_ms=result.latency_ms,
        decided_by=result.decided_by,
        policy_version=policy_version,
    )


def _turn_summary(
    request: CanonicalRequest,
    decision: Decision,
    policy: Policy,
    policy_version: str,
    *,
    usage: Usage | None,
    tokens: int,
    cost: float | None,
) -> AuditRecord:
    """The agent and economic fact row of one checkpoint (backend/docs/observability.md)."""
    results = decision.results
    if decision.action == Action.ALLOW:
        reason = "allowed" if results else "allowed: no check enabled at this checkpoint"
    else:
        by = ", ".join(r.check for r in results if r.action == decision.action)
        reason = f"{_PAST[decision.action]} by {by}"
    # Usage goes on this one row per request, so summing the audit table does not double count.
    if request.checkpoint not in _USAGE_CHECKPOINTS:
        usage, tokens, cost = None, 0, 0.0
    if usage is not None:
        tokens = usage.prompt_tokens + usage.completion_tokens
    if cost is None:
        model = policy.models.get(request.model)
        cost = tokens / 1000 * model.price_per_1k_tokens if model else 0.0
    overhead = sum(r.latency_ms for r in results)
    assistant = [m for m in request.messages if m.role == "assistant"]
    reply_calls = request.reply.tool_calls if request.reply else []
    return AuditRecord(
        ts=datetime.now(UTC).isoformat(),
        request_id=request.request_id,
        caller_id=request.caller_id,
        model=request.model,
        checkpoint=request.checkpoint,
        check=TURN_SUMMARY,
        action=decision.action,
        reason=reason,
        score=max((r.score for r in results), default=0.0),
        latency_ms=overhead,
        # A block stops the pipeline, so the blocking result is the last one.
        decided_by=results[-1].decided_by if decision.blocked_by else DecidedBy.RULES,
        tokens=tokens,
        cost=cost,
        policy_version=policy_version,
        conversation_id=conversation_id(request.caller_id, request.messages),
        step=len(assistant),
        messages=len(request.messages),
        tool_calls=len(reply_calls) if request.reply else None,
        tools=",".join(c.name for c in reply_calls) or None,
        tool_calls_total=sum(len(m.tool_calls) for m in assistant) + len(reply_calls),
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        upstream_latency_ms=usage.upstream_latency_ms if usage else None,
        overhead_ms=overhead,
        blocked_by=decision.blocked_by,
    )


class Pipeline:
    """The check pipeline bound to the application's long-lived dependencies.

    The proxy calls `run` once per checkpoint with the policy snapshot the
    request took at its start, so a hot reload never mixes two policy versions in one request.
    """

    def __init__(
        self,
        *,
        audit: AuditSink,
        ledger: UsageLedger | None = None,
        signatures: Callable[[], list[Signature] | None] | None = None,
        judge: Judge | None = None,
        checks: list[Check] | None = None,
    ) -> None:
        self._audit = audit
        self._ledger = ledger
        self._signatures = signatures
        self._judge = judge
        self._checks = checks

    async def run(
        self,
        request: CanonicalRequest,
        snapshot: PolicySnapshot,
        caller: Caller = NO_CALLER,
        *,
        usage: Usage | None = None,
    ) -> tuple[Decision, CanonicalRequest]:
        """The decision for `request.checkpoint` and the request to forward (redactions applied)."""
        return await run_checkpoint(
            request,
            snapshot.policy,
            snapshot.version,
            caller,
            ledger=self._ledger,
            signatures=self._signatures() if self._signatures is not None else None,
            judge=self._judge,
            audit=self._audit,
            checks=self._checks,
            usage=usage,
        )
