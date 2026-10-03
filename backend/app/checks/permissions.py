"""Permissions: the user's roles must allow the tools the model asks for (docs/architecture.md §4).

Runs at `tool_call`, against the calls in the upstream reply. By then the proxy has already taken
the tools the user may not use out of the request and replaced any denied call by a notice, so
this check is the last line of defence, and the one the tool guard (/v1/tools/check) relies on.

Tool calls already in the conversation's assistant messages are deliberately not re-checked at
`input` / `tool_result`. Each was judged at its own `tool_call` checkpoint, and execution is gated
by the tool guard, not by history. Rejecting history would not stop a forged call either: the
agent can paste the same output into a user message, whose text the content checks see anyway. It
would only lock out a session after a policy edit narrows what a role may use.
"""

from __future__ import annotations

import time
from typing import Any

from app.checks.base import CheckContext, make_result, safe_label
from app.models import CanonicalRequest, Checkpoint, CheckResult


class PermissionsCheck:
    id = "permissions"
    cost_rank = 1
    checkpoints = frozenset({Checkpoint.TOOL_CALL})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        role = ctx.caller_role
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


CHECK = PermissionsCheck()
