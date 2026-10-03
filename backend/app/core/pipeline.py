import logging
from collections.abc import Callable, Sequence

from app.models import (
    Action,
    CanonicalRequest,
    CheckConfig,
    Checkpoint,
    CheckResult,
    Decision,
    Mode,
    Redaction,
)
from app.protocols.check import Check

logger = logging.getLogger(__name__)

# (check id, checkpoint) -> its mode and settings. The policy will provide it.
ConfigLookup = Callable[[str, Checkpoint], CheckConfig]

_STRENGTH = {Action.ALLOW: 0, Action.FLAG: 1, Action.REDACT: 2, Action.BLOCK: 3}


def _final_action(verdict: str, mode: Mode) -> Action:
    """The mode table from docs/architecture.md section 4."""
    if verdict == "allow":
        return Action.ALLOW
    if mode is Mode.MONITOR:
        return Action.FLAG
    if verdict == "redact":
        return Action.REDACT
    return Action.BLOCK  # a block, or an error: fail closed


def _error_result(check_id: str, checkpoint: Checkpoint, reason: str) -> CheckResult:
    return CheckResult(
        check=check_id,
        checkpoint=checkpoint,
        verdict="error",
        action=Action.BLOCK,  # overwritten by the mode table
        score=1.0,
        reason=reason,
        latency_ms=0.0,
    )


def _apply_redactions(request: CanonicalRequest, redactions: list[Redaction]) -> CanonicalRequest:
    """Return a copy with the redactions applied. Raises ValueError if any is invalid."""
    updated = request.model_copy(deep=True)
    by_message: dict[int, list[Redaction]] = {}
    for redaction in redactions:
        by_message.setdefault(redaction.message_index, []).append(redaction)

    for index, items in by_message.items():
        if index == -1:
            message = updated.reply
        elif 0 <= index < len(updated.messages):
            message = updated.messages[index]
        else:
            raise ValueError(f"message_index {index} is out of range")
        if message is None or message.content is None:
            raise ValueError(f"message {index} has no content to redact")

        content = message.content
        limit = len(content)
        # Apply from the end so earlier offsets stay valid. Overlaps fail the range check.
        for item in sorted(items, key=lambda r: r.start, reverse=True):
            if not 0 <= item.start <= item.end <= limit:
                raise ValueError(f"redaction {item.start}-{item.end} is invalid or overlaps")
            content = content[: item.start] + item.replacement + content[item.end :]
            limit = item.start
        message.content = content
    return updated


class Pipeline:
    """Runs the checks of one checkpoint, cheapest first, and stops at the first block."""

    def __init__(self, checks: Sequence[Check], config_for: ConfigLookup) -> None:
        self._checks = sorted(checks, key=lambda check: check.cost_rank)
        self._config_for = config_for

    async def run(self, request: CanonicalRequest) -> tuple[Decision, CanonicalRequest]:
        """Returns the decision and the request with redactions applied (the copy to forward)."""
        working = request
        results: list[CheckResult] = []
        blocked_by: str | None = None

        for check in self._checks:
            if request.checkpoint not in check.checkpoints:
                continue
            config = self._config_for(check.id, request.checkpoint)
            if config.mode is Mode.OFF:
                continue

            result, working = await self._evaluate(check, working, config)
            results.append(result)
            if result.action is Action.BLOCK:
                blocked_by = check.id
                break

        action = max((r.action for r in results), key=_STRENGTH.__getitem__, default=Action.ALLOW)
        decision = Decision(
            request_id=request.request_id,
            checkpoint=request.checkpoint,
            action=action,
            blocked_by=blocked_by,
            results=results,
        )
        return decision, working

    async def _evaluate(
        self, check: Check, request: CanonicalRequest, config: CheckConfig
    ) -> tuple[CheckResult, CanonicalRequest]:
        try:
            result = await check.run(request, config.settings)
        except Exception:  # a failing check must not crash the request; it fails closed below
            logger.exception("check %s raised at checkpoint %s", check.id, request.checkpoint)
            result = _error_result(check.id, request.checkpoint, "check raised an exception")

        action = _final_action(result.verdict, config.mode)
        if action is Action.REDACT:
            try:
                request = _apply_redactions(request, result.redactions)
            except ValueError as exc:
                logger.error("check %s returned invalid redactions: %s", check.id, exc)
                result = _error_result(check.id, request.checkpoint, f"invalid redactions: {exc}")
                action = _final_action(result.verdict, config.mode)
        return result.model_copy(update={"action": action}), request
