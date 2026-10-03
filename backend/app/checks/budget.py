"""Per-caller budgets: requests per minute, tokens per day, cost per day (docs/architecture.md §4).

Limits come from the caller's policy budgets via the context; usage comes from the ledger. The
check only reads usage. The proxy records it, so this check never counts the request it judges.
It runs at `input` and `tool_result`: both are requests the proxy forwards upstream, so a trailing
tool message must not skip the budget.
"""

from __future__ import annotations

import time
from typing import Any

from app.checks.base import CheckContext, make_result
from app.models import CanonicalRequest, Checkpoint, CheckResult


def _fmt(value: float) -> str:
    # Accumulated float cost (0.1 + 0.2) would otherwise show as 0.30000000000000004.
    return str(round(value, 6))


class BudgetCheck:
    id = "budget"
    cost_rank = 2
    checkpoints = frozenset({Checkpoint.input, Checkpoint.tool_result})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        limits = {
            "requests_per_minute": ctx.requests_per_minute,
            "tokens_per_day": ctx.tokens_per_day,
            "cost_per_day": ctx.cost_per_day,
        }
        if all(limit is None for limit in limits.values()):
            return make_result(self.id, request, "allow", "no budget limits set", started)
        if ctx.ledger is None:
            # Fail closed: a limit exists but there is no usage to compare it with.
            return make_result(
                self.id,
                request,
                "error",
                "usage ledger unavailable, budgets cannot be checked",
                started,
            )

        ledger = ctx.ledger
        readers = {
            "requests_per_minute": ledger.requests_last_minute,
            "tokens_per_day": ledger.tokens_today,
            "cost_per_day": ledger.cost_today,
        }
        exhausted: list[str] = []
        within: list[str] = []
        for name, limit in limits.items():
            if limit is None:
                continue
            used = readers[name](request.caller_id)
            usage = f"{_fmt(used)}/{_fmt(limit)}"
            # At the limit means the budget is spent, so the next request is the one refused.
            if used >= limit:
                exhausted.append(f"{name} exhausted: {usage}")
            else:
                within.append(f"{name} {usage}")

        if exhausted:
            return make_result(self.id, request, "block", "; ".join(exhausted), started)
        reason = "within budget: " + ", ".join(within)
        return make_result(self.id, request, "allow", reason, started)


CHECK = BudgetCheck()
