"""Aggregation behind GET /api/metrics (contracts/http-api.md, docs/architecture.md §8).

Pure function over audit records: nothing is stored separately and nothing is read here.

Definitions the numbers rely on:
- A system-event row has a `check` in SYSTEM_EVENTS, or no checkpoint (a CheckResult always has
  one). It never counts in per-check or per-request stats, only under `system_events`.
- A request is a distinct non-null request_id with at least one check row. Its outcome is the
  strongest action among its check rows (block > redact > flag > allow). A request that only
  produced system events (e.g. a malformed body) shows up under `system_events`, so
  `requests == allowed + redacted + blocked + flagged` always holds.
- Upstream usage is on a request's `turn_summary` row (older logs: on one or several of its reply
  check rows), so tokens and cost are taken once per request, from its row with the most tokens.
  The same usage on a check row and on the turn_summary row therefore never counts twice.
- Percentiles use the nearest-rank method: the value at rank ceil(p/100 * n) of the sorted
  values. No interpolation, so p50 of two values is the lower one. Empty input gives 0.0.
- `since` narrows every figure except `budget_by_caller` and `economics.budget_utilization`,
  which always cover the UTC day of `now` because they are compared against per-day limits.

`agents` and `economics` come from `turn_summary` rows only (one per checkpoint, written by the
pipeline). A turn is one request, i.e. one agent step: its turn_summary rows grouped by
request_id. Its outcome is the strongest action of those rows, its overhead their summed check
latency, its usage and tool calls those of its reply row. Its session is the conversation_id of
its input/tool_result row, which sees the conversation exactly as the agent sent it. Steps of a
session are the turns seen in it. `overhead_share` covers the turns that reached the upstream:
overhead / (overhead + upstream latency).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.proxy import ANONYMOUS_CALLER
from app.models import TURN_SUMMARY, Action, AuditRecord, Checkpoint, DecidedBy
from app.models.policy import Policy

# AuditRecord.check values that are system events, not check results (contracts/models.md).
SYSTEM_EVENTS = frozenset(
    {
        "bad_request",
        "upstream_unavailable",
        "policy_loaded",
        "policy_rejected",
        "signature_feed_updated",
        "signature_feed_failed",
        TURN_SUMMARY,
    }
)
# Checkpoints before the upstream call: a block there means the paid model was never called.
_REQUEST_CHECKPOINTS = frozenset({Checkpoint.INPUT, Checkpoint.TOOL_RESULT})
# The AI decision maker's check id. SC-003 measures rule overhead without it.
AI_CHECK = "jev"
# Presentation limit for the dashboard list, not a decision parameter.
TOP_BLOCK_REASONS = 10

_STRENGTH = {Action.ALLOW: 0, Action.FLAG: 1, Action.REDACT: 2, Action.BLOCK: 3}
_OUTCOME = {
    Action.ALLOW: "allowed",
    Action.REDACT: "redacted",
    Action.BLOCK: "blocked",
    Action.FLAG: "flagged",
}

_Row = tuple[AuditRecord, datetime]


def percentile(values: list[float], p: int) -> float:
    """Nearest-rank percentile (p in 1..100). Integer rank math keeps it exact and deterministic."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, (p * len(ordered) + 99) // 100)
    return float(ordered[rank - 1])


def _p50_p95(values: list[float]) -> dict[str, float]:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95)}


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    # The audit log writes UTC; a naive timestamp is read as UTC rather than local time.
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _is_system_event(record: AuditRecord) -> bool:
    return record.check in SYSTEM_EVENTS or record.checkpoint is None


def _usage_rows(rows: list[_Row]) -> list[_Row]:
    """One row per request carrying its upstream usage, plus every row without a request id."""
    best: dict[str, _Row] = {}
    loose: list[_Row] = []
    for record, ts in rows:
        if record.request_id is None:
            loose.append((record, ts))
            continue
        current = best.get(record.request_id)
        if current is None or (record.tokens, record.cost) > (current[0].tokens, current[0].cost):
            best[record.request_id] = (record, ts)
    return [*best.values(), *loose]


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _ratio(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _pct(used: float, limit: float | None) -> float | None:
    # None (no limit) or 0 (nothing allowed) has no meaningful utilisation.
    return 100.0 * used / limit if limit else None


@dataclass
class _Turn:
    """One request (agent step), from its turn_summary rows."""

    caller_id: str | None
    conversation_id: str | None
    action: Action
    tool_calls: int
    tools: list[str]
    tokens: int
    cost: float
    upstream_ms: float | None
    overhead_ms: float


def _turn(records: list[AuditRecord]) -> _Turn:
    # A reply row may carry the redacted copy of the conversation; the request row never does.
    first = next((r for r in records if r.checkpoint in _REQUEST_CHECKPOINTS), records[0])
    upstream = [r.upstream_latency_ms for r in records if r.upstream_latency_ms is not None]
    return _Turn(
        caller_id=first.caller_id,
        conversation_id=first.conversation_id,
        action=max((r.action for r in records), key=_STRENGTH.__getitem__),
        tool_calls=sum(r.tool_calls or 0 for r in records),
        tools=[name for r in records if r.tools for name in r.tools.split(",")],
        tokens=sum(r.tokens for r in records),
        cost=sum(r.cost for r in records),
        upstream_ms=sum(upstream) if upstream else None,
        overhead_ms=sum(r.overhead_ms or 0.0 for r in records),
    )


def _agents(turns: list[_Turn]) -> dict[str, dict[str, Any]]:
    by_caller: dict[str, list[_Turn]] = defaultdict(list)
    for turn in turns:
        if turn.caller_id is not None:
            by_caller[turn.caller_id].append(turn)
    agents: dict[str, dict[str, Any]] = {}
    for caller_id, own in sorted(by_caller.items()):
        steps = Counter(t.conversation_id for t in own if t.conversation_id is not None)
        outcomes = Counter(t.action for t in own)
        reached = [t for t in own if t.upstream_ms is not None]
        upstream = sum(t.upstream_ms or 0.0 for t in reached)
        reached_overhead = sum(t.overhead_ms for t in reached)
        agents[caller_id] = {
            "sessions": len(steps),
            "turns": len(own),
            "avg_steps_per_session": _ratio(sum(steps.values()), len(steps)),
            "max_steps": max(steps.values(), default=0),
            "tool_calls": sum(t.tool_calls for t in own),
            "tool_usage": _sorted_counts(Counter(name for t in own for name in t.tools)),
            "blocked_turns": outcomes[Action.BLOCK],
            "block_rate": _ratio(outcomes[Action.BLOCK], len(own)),
            "redacted_turns": outcomes[Action.REDACT],
            "flagged_turns": outcomes[Action.FLAG],
            "tokens": sum(t.tokens for t in own),
            "cost": float(sum(t.cost for t in own)),
            "avg_upstream_latency_ms": _ratio(upstream, len(reached)),
            "avg_overhead_ms": _ratio(sum(t.overhead_ms for t in own), len(own)),
            "overhead_share": _ratio(reached_overhead, reached_overhead + upstream),
        }
    return agents


def _economics(
    summary_rows: list[_Row],
    turns: list[_Turn],
    policy: Policy,
    tokens_today: Counter[str],
    cost_today: dict[str, float],
) -> dict[str, Any]:
    budget = policy.check_config("budget").params
    cost_by_model: dict[str, float] = defaultdict(float)
    cost_by_caller: dict[str, float] = defaultdict(float)
    cost_by_day: dict[str, float] = defaultdict(float)
    tokens_by_model: dict[str, dict[str, int]] = {}
    blocked_before_upstream = 0
    # Usage is only on reply rows, one per request, so summing the rows counts it once.
    for record, ts in summary_rows:
        if record.model is not None:
            cost_by_model[record.model] += record.cost
            tokens = tokens_by_model.setdefault(
                record.model, {"prompt": 0, "completion": 0, "total": 0}
            )
            tokens["prompt"] += record.prompt_tokens or 0
            tokens["completion"] += record.completion_tokens or 0
            tokens["total"] += record.tokens
        if record.caller_id is not None:
            cost_by_caller[record.caller_id] += record.cost
        cost_by_day[ts.date().isoformat()] += record.cost
        if record.action == Action.BLOCK and record.checkpoint in _REQUEST_CHECKPOINTS:
            blocked_before_upstream += 1
    session_cost: dict[str, float] = defaultdict(float)
    for turn in turns:
        if turn.conversation_id is not None:
            session_cost[turn.conversation_id] += turn.cost
    per_session = list(session_cost.values())
    return {
        "cost_by_model": dict(sorted(cost_by_model.items())),
        "cost_by_caller": dict(sorted(cost_by_caller.items())),
        "cost_by_day": dict(sorted(cost_by_day.items())),
        "tokens_by_model": dict(sorted(tokens_by_model.items())),
        "cost_per_session": {
            **_p50_p95(per_session),
            "avg": _ratio(sum(per_session), len(per_session)),
        },
        "cost_per_turn_avg": _ratio(sum(t.cost for t in turns), len(turns)),
        "budget_utilization": {
            ANONYMOUS_CALLER: {
                "tokens_pct": _pct(tokens_today[ANONYMOUS_CALLER], budget.get("tokens_per_day")),
                "cost_pct": _pct(cost_today[ANONYMOUS_CALLER], budget.get("cost_per_day")),
            }
        },
        "blocked_before_upstream": blocked_before_upstream,
        "price_per_1k_tokens": {
            m: cfg.price_per_1k_tokens for m, cfg in sorted(policy.models.items())
        },
    }


def compute_metrics(
    records: Iterable[AuditRecord],
    policy: Policy,
    *,
    since: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the GET /api/metrics body. Raises ValueError if `since` is not ISO 8601."""
    now = now or datetime.now(UTC)
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    all_rows: list[_Row] = [(r, _parse_ts(r.ts)) for r in records]
    since_dt = _parse_ts(since) if since else None
    rows = [row for row in all_rows if since_dt is None or row[1] >= since_dt]
    check_rows = [row for row in rows if not _is_system_event(row[0])]

    # Per-check stats.
    latencies: dict[str, list[float]] = defaultdict(list)
    actions_by_check: dict[str, dict[str, int]] = {}
    decided_by = {d.value: 0 for d in DecidedBy}
    blocks_by_check: Counter[str] = Counter()
    blocks_by_checkpoint: Counter[str] = Counter()
    blocks_by_caller: Counter[str] = Counter()
    block_reasons: Counter[tuple[str, str]] = Counter()
    by_request: dict[str, list[_Row]] = defaultdict(list)
    for record, ts in check_rows:
        latencies[record.check].append(record.latency_ms)
        counts = actions_by_check.setdefault(record.check, {a.value: 0 for a in Action})
        counts[record.action.value] += 1
        decided_by[record.decided_by.value] += 1
        if record.action == Action.BLOCK:
            blocks_by_check[record.check] += 1
            if record.checkpoint is not None:  # always set: system events are filtered out above
                blocks_by_checkpoint[record.checkpoint.value] += 1
            if record.caller_id is not None:
                blocks_by_caller[record.caller_id] += 1
            block_reasons[(record.check, record.reason)] += 1
        if record.request_id is not None:
            by_request[record.request_id].append((record, ts))

    # Per-request stats: outcome, overhead and the minute the request started in.
    totals = {"requests": len(by_request), "allowed": 0, "redacted": 0, "blocked": 0, "flagged": 0}
    overhead: list[float] = []
    rules_overhead: list[float] = []
    minutes: dict[datetime, dict[str, int]] = {}
    for req_rows in by_request.values():
        outcome = _OUTCOME[max((r.action for r, _ in req_rows), key=_STRENGTH.__getitem__)]
        totals[outcome] += 1
        overhead.append(sum(r.latency_ms for r, _ in req_rows))
        rules_overhead.append(sum(r.latency_ms for r, _ in req_rows if r.check != AI_CHECK))
        start = min(ts for _, ts in req_rows).replace(second=0, microsecond=0)
        bucket = minutes.setdefault(
            start, {"requests": 0, "blocked": 0, "redacted": 0, "flagged": 0}
        )
        bucket["requests"] += 1
        if outcome != "allowed":
            bucket[outcome] += 1

    # Usage: the window total follows `since`; the budget always covers today (UTC).
    window_usage = _usage_rows(rows)
    today = now.date()
    tokens_today: Counter[str] = Counter()
    cost_today: dict[str, float] = defaultdict(float)
    for record, ts in _usage_rows(all_rows):
        if record.caller_id is not None and ts.date() == today:
            tokens_today[record.caller_id] += record.tokens
            cost_today[record.caller_id] += record.cost

    top_reasons = sorted(block_reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    check_ids = list(dict.fromkeys([*policy.checks, *policy.profile.checks]))

    # Agent and economic facts: the turn_summary rows, grouped into turns by request.
    summary_rows = [row for row in rows if row[0].check == TURN_SUMMARY]
    by_turn: dict[str, list[AuditRecord]] = defaultdict(list)
    for record, _ in summary_rows:
        if record.request_id is not None:
            by_turn[record.request_id].append(record)
    turns = [_turn(records) for records in by_turn.values()]
    budget = policy.check_config("budget").params

    return {
        "active_profile": policy.active_profile,
        "totals": totals,
        "blocks_by_check": _sorted_counts(blocks_by_check),
        "latency_ms_by_check": {c: _p50_p95(v) for c, v in sorted(latencies.items())},
        "overhead_ms": _p50_p95(overhead),
        "budget_by_caller": {
            ANONYMOUS_CALLER: {
                "tokens_today": tokens_today[ANONYMOUS_CALLER],
                "tokens_limit": budget.get("tokens_per_day") or 0,
                "cost_today": cost_today[ANONYMOUS_CALLER],
                "cost_limit": float(budget.get("cost_per_day") or 0.0),
            }
        },
        # Additive fields beyond the contract shape.
        "enabled_checks": {
            cid: {cp.value: policy.mode(cid, cp).value for cp in Checkpoint} for cid in check_ids
        },
        "blocks_by_checkpoint": _sorted_counts(blocks_by_checkpoint),
        "blocks_by_caller": _sorted_counts(blocks_by_caller),
        "actions_by_check": dict(sorted(actions_by_check.items())),
        "decided_by": decided_by,
        "rules_overhead_ms": _p50_p95(rules_overhead),
        "jev_latency_ms": _p50_p95(latencies.get(AI_CHECK, [])),
        "system_events": _sorted_counts(Counter(r.check for r, _ in rows if _is_system_event(r))),
        "timeline": [
            {"minute": m.strftime("%Y-%m-%dT%H:%M:00Z"), **counts}
            for m, counts in sorted(minutes.items())
        ],
        "top_block_reasons": [
            {"check": check, "reason": reason, "count": n}
            for (check, reason), n in top_reasons[:TOP_BLOCK_REASONS]
        ],
        "tokens_total": sum(r.tokens for r, _ in window_usage),
        "cost_total": float(sum(r.cost for r, _ in window_usage)),
        "policy_versions": _sorted_counts(Counter(r.policy_version for r, _ in rows)),
        "agents": _agents(turns),
        "economics": _economics(summary_rows, turns, policy, tokens_today, cost_today),
    }
