"""Budgets: requests per minute, tokens per day, cost per day (docs/architecture.md §4).

Limits are per role (`checks.budget.roles`). A user gets the most generous of their roles: per
limit, the highest value, and no limit at all if one of their roles leaves it out. A user none of
whose roles has a budget is blocked (fail closed). Usage comes from the ledger, keyed by the
request's caller_id (the token's user). The check only reads usage. The proxy records it, so this
check never counts the request it judges. It runs at `input` and `tool_result`: both are requests
the proxy forwards upstream, so a trailing tool message must not skip the budget.
"""

from __future__ import annotations

import time
from typing import Any

from app.checks.base import CheckContext, make_result, safe_label
from app.models import CanonicalRequest, Checkpoint, CheckResult

_LIMITS = ("requests_per_minute", "tokens_per_day", "cost_per_day")


def _most_generous(budgets: list[dict[str, Any]], name: str) -> float | None:
    """The highest limit among the budgets; None (unlimited) if one of them leaves it out."""
    values = [b.get(name) for b in budgets]
    limits = [v for v in values if v is not None]
    return None if len(limits) < len(values) else max(limits)


def _fmt(value: float) -> str:
    # Accumulated float cost (0.1 + 0.2) would otherwise show as 0.30000000000000004.
    return str(round(value, 6))


class BudgetCheck:
    id = "budget"
    cost_rank = 2
    checkpoints = frozenset({Checkpoint.INPUT, Checkpoint.TOOL_RESULT})

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        per_role = settings.get("roles", {})
        budgets = [per_role[r] for r in ctx.roles if isinstance(per_role, dict) and r in per_role]
        if not budgets:
            roles = ", ".join(repr(safe_label(r)) for r in ctx.roles) or "none"
            reason = f"no budget for roles {roles}"
            return make_result(self.id, request, "block", reason, started)
        limits = {name: _most_generous(budgets, name) for name in _LIMITS}
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
