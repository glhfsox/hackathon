"""Loop detection: too many tool calls, or the same call repeated (docs/architecture.md §4).

The agent re-sends the whole conversation every step, so the history in the request is enough;
no state is kept between requests.

Both limits count one agent turn: the tool calls after the last user message. A runaway agent
loops without the user, so it is caught inside its turn, while a long chat whose every question
needs a tool or two is not capped for the whole session (budgets bound the session). At `input`
the new user message has just opened a turn, so nothing counts yet. Without any user message the
whole conversation is one turn.

Two calls are repeats when the tool name matches exactly and the arguments are equal after
ignoring key order and normalizing whitespace in string values (stripped, runs collapsed to one
space), so padding a command with spaces does not make it look new. Case is kept: commands and
identifiers can be case sensitive. A deliberately varied argument (a counter, a nonce) makes every
call distinct by design; only `max_tool_calls` bounds that.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from app.checks.base import CheckContext, make_result
from app.models import CanonicalRequest, Checkpoint, CheckResult

_LIMIT_KEYS = ("max_tool_calls", "max_repeats")


def _normalize(value: Any) -> Any:
    """Strip and collapse whitespace in every string value, recursively. Keys are left as is."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


class LoopDetectionCheck:
    id = "loop_detection"
    cost_rank = 3
    checkpoints = frozenset({Checkpoint.input, Checkpoint.tool_result})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        # Check params are untyped in the policy schema, so a bad edit must surface as an error.
        for key in _LIMIT_KEYS:
            value = settings.get(key)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                return make_result(
                    self.id,
                    request,
                    "error",
                    f"invalid setting {key}: {value!r} (expected a non-negative integer)",
                    started,
                )
        max_calls: int | None = settings.get("max_tool_calls")
        max_repeats: int | None = settings.get("max_repeats")
        if max_calls is None and max_repeats is None:
            return make_result(self.id, request, "allow", "no loop limits set", started)

        turn_start = 1 + max(
            (i for i, message in enumerate(request.messages) if message.role == "user"),
            default=-1,
        )
        # Sorted keys make {"a": 1, "b": 2} and {"b": 2, "a": 1} the same call.
        calls: Counter[tuple[str, str]] = Counter(
            (call.name, json.dumps(_normalize(call.arguments), sort_keys=True, ensure_ascii=False))
            for message in request.messages[turn_start:]
            if message.role == "assistant"
            for call in message.tool_calls
        )
        total = sum(calls.values())
        (top_name, _), top_count = calls.most_common(1)[0] if calls else (("", ""), 0)

        violations: list[str] = []
        if max_calls is not None and total > max_calls:
            violations.append(
                f"max_tool_calls exceeded: {total} tool calls since the last user message "
                f"(limit {max_calls})"
            )
        # Arguments stay out of the reason: they may carry data the audit log should not hold.
        if max_repeats is not None and top_count > max_repeats:
            violations.append(
                f"max_repeats exceeded: {top_name} called {top_count} times with identical "
                f"arguments since the last user message (limit {max_repeats})"
            )
        if violations:
            return make_result(self.id, request, "block", "; ".join(violations), started)
        return make_result(
            self.id,
            request,
            "allow",
            f"{total} tool calls since the last user message, at most {top_count} identical",
            started,
        )


CHECK = LoopDetectionCheck()
