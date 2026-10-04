"""Permissions: the request may use this model and these tools (docs/architecture.md §4, #1).

The models come from the check's own policy parameter `allowed_models` (left out: none). The
tools are those the user's roles allow (`roles` / `permissions` in the policy, resolved from the
token by the proxy, which has already taken disallowed tools out of the request and replaced a
disallowed call by a notice): this check is the last line of defence.

The model is checked at `input` and `tool_result`: both are requests the proxy forwards upstream
to `request.model`, and a trailing tool message must not skip the allow-list. Tools are checked at
`tool_call`, against the calls in the upstream reply.

Tool calls already in the conversation's assistant messages are deliberately not re-checked at
`input` / `tool_result`. Each was judged at its own `tool_call` checkpoint. Rejecting history
would not stop a forged call either: the agent can paste the same output into a user message,
whose text the content checks see anyway. It would only lock out a session after a policy edit
narrows `allowed_tools`.
"""

from __future__ import annotations

import time
from typing import Any

from app.checks.base import CheckContext, make_result, safe_label
from app.models import CanonicalRequest, Checkpoint, CheckResult


class PermissionsCheck:
    id = "permissions"
    cost_rank = 1
    checkpoints = frozenset({Checkpoint.INPUT, Checkpoint.TOOL_CALL, Checkpoint.TOOL_RESULT})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        role = ctx.caller_role
        if request.checkpoint in (Checkpoint.INPUT, Checkpoint.TOOL_RESULT):
            if request.model not in settings.get("allowed_models", []):
                reason = f"model {safe_label(request.model)!r} is not allowed"
                return make_result(self.id, request, "block", reason, started)
            reason = f"model {safe_label(request.model)!r} is allowed"
            return make_result(self.id, request, "allow", reason, started)

        if request.checkpoint == Checkpoint.TOOL_CALL:
            calls = request.reply.tool_calls if request.reply else []
            if not calls:
                reason = "tool_call checkpoint without tool calls in the reply"
                return make_result(self.id, request, "error", reason, started)
            denied = [c.name for c in calls if c.name not in ctx.allowed_tools]
            if denied:
                names = ", ".join(repr(safe_label(n)) for n in dict.fromkeys(denied))
                reason = f"tool {names} is not allowed for role {role!r}"
                return make_result(self.id, request, "block", reason, started)
            names = ", ".join(repr(safe_label(c.name)) for c in calls)
            reason = f"tool {names} is allowed for role {role!r}"
            return make_result(self.id, request, "allow", reason, started)

        reason = f"permissions does not run at checkpoint {request.checkpoint.value}"
        return make_result(self.id, request, "error", reason, started)


CHECK = PermissionsCheck()
