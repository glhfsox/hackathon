import json
from datetime import UTC, datetime

import pytest

from app.core.metrics import compute_metrics, percentile
from app.models import AuditRecord
from app.models.policy import Policy

NOW = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)

# The shared dev policy (backend/policy.yaml as described in the team brief).
DEV_POLICY = {
    "version": "0.1",
    "active_profile": "balanced",
    "profiles": {
        "strict": {
            "jev_threshold": 0.4,
            "checks": {
                "pii_secrets": {"input": "block", "tool_result": "block", "output": "block"}
            },
        },
        "balanced": {"jev_threshold": 0.6},
        "permissive": {"jev_threshold": 0.8},
    },
    "models": {"gemma4": {"upstream_base_url": "http://localhost:11434/v1"}},
    "callers": {
        "demo": {
            "api_key_env": "DEMO_API_KEY",
            "role": "developer",
            "allowed_models": ["gemma4"],
            "budgets": {"requests_per_minute": 60, "tokens_per_day": 200000, "cost_per_day": 1.0},
        },
        "support": {
            "api_key_env": "SUPPORT_API_KEY",
            "role": "support",
            "allowed_models": ["gemma4"],
            "budgets": {"requests_per_minute": 30, "tokens_per_day": 50000, "cost_per_day": 0.5},
        },
        "playground": {
            "api_key_env": "PLAYGROUND_API_KEY",
            "role": "developer",
            "allowed_models": ["gemma4"],
            "budgets": {"requests_per_minute": 60, "tokens_per_day": 200000, "cost_per_day": 1.0},
        },
    },
    "checks": {
        "permissions": {"input": "block", "tool_call": "block"},
        "budget": {"input": "block"},
        "loop_detection": {"input": "block", "tool_result": "block", "max_tool_calls": 10},
        "signatures": {"input": "block", "tool_call": "block", "tool_result": "block"},
        "tool_args": {"tool_call": "block", "allowed_root": "/workspace"},
        "pii_secrets": {"input": "redact", "tool_result": "redact", "output": "redact"},
        "jev": {"input": "block", "tool_result": "block", "output": "monitor"},
    },
    "jev": {"fallback": {"model": "gemma4", "base_url": "http://localhost:11434/v1"}},
}


def _policy(**over) -> Policy:
    return Policy.model_validate({**DEV_POLICY, **over})


def _rec(ts: str, check: str, action: str = "allow", **kw) -> AuditRecord:
    kw.setdefault("policy_version", "0.1")
    kw.setdefault("reason", "ok")
    return AuditRecord(ts=ts, check=check, action=action, **kw)


def _req(rid: str, caller: str, ts: str, checkpoint: str, check: str, action: str, **kw):
    return _rec(
        ts, check, action, request_id=rid, caller_id=caller, checkpoint=checkpoint, model="gemma4",
        **kw,
    )  # fmt: skip


def _dataset() -> list[AuditRecord]:
    day = "2026-10-03T"
    return [
        # r1: allowed; the reply's usage is stamped on both output rows and must count once.
        _req("r1", "demo", day + "12:00:05Z", "input", "permissions", "allow", latency_ms=0.5),
        _req("r1", "demo", day + "12:00:05.5Z", "input", "jev", "allow",
             latency_ms=100.0, decided_by="jev"),
        _req("r1", "demo", day + "12:00:06Z", "output", "pii_secrets", "allow",
             latency_ms=1.0, tokens=1000, cost=0.5),
        _req("r1", "demo", day + "12:00:06Z", "output", "jev", "allow",
             latency_ms=50.0, decided_by="fallback", tokens=1000, cost=0.5),
        # r2: redacted at tool_result
        _req("r2", "demo", day + "12:00:40Z", "tool_result", "loop_detection", "allow",
             latency_ms=0.25),
        _req("r2", "demo", day + "12:00:40Z", "tool_result", "pii_secrets", "redact",
             latency_ms=2.0, reason="redacted SSN"),
        _req("r2", "demo", day + "12:00:41Z", "output", "pii_secrets", "allow",
             latency_ms=0.75, tokens=500, cost=0.25),
        # r3: blocked at tool_call by permissions
        _req("r3", "support", day + "12:01:10Z", "input", "permissions", "allow", latency_ms=0.5),
        _req("r3", "support", day + "12:01:10Z", "input", "jev", "allow",
             latency_ms=200.0, decided_by="jev"),
        _req("r3", "support", day + "12:01:11Z", "tool_call", "permissions", "block",
             latency_ms=0.5, tokens=300, reason="tool run_shell not allowed"),
        # r4: flagged by a monitor-mode signature hit, under the new policy version
        _req("r4", "demo", day + "12:01:30Z", "input", "signatures", "flag",
             latency_ms=1.0, reason="sig-001 matched", policy_version="0.2"),
        _req("r4", "demo", day + "12:01:31Z", "output", "pii_secrets", "allow",
             latency_ms=0.25, tokens=100, policy_version="0.2"),
        # system events
        _rec(day + "11:59:00Z", "policy_loaded"),
        _rec(day + "12:01:20Z", "policy_loaded", policy_version="0.2"),
        _rec(day + "12:00:30Z", "auth_failed", "block", latency_ms=0.1, reason="unknown key"),
        # A system event with a checkpoint and a request id that never reached the pipeline.
        _rec(day + "12:01:40Z", "upstream_unavailable", "block", request_id="r5",
             caller_id="demo", checkpoint="input", latency_ms=5.0, policy_version="0.2"),
    ]  # fmt: skip


CONTRACT_KEYS = {
    "active_profile",
    "totals",
    "blocks_by_check",
    "latency_ms_by_check",
    "overhead_ms",
    "budget_by_caller",
}
EXTRA_KEYS = {
    "enabled_checks",
    "blocks_by_checkpoint",
    "blocks_by_caller",
    "actions_by_check",
    "decided_by",
    "rules_overhead_ms",
    "jev_latency_ms",
    "system_events",
    "timeline",
    "top_block_reasons",
    "tokens_total",
    "cost_total",
    "policy_versions",
    "agents",
    "economics",
}
ZERO = {"p50": 0.0, "p95": 0.0}


def _budget(tokens: int, tokens_limit: int, cost: float, cost_limit: float) -> dict:
    return {
        "tokens_today": tokens,
        "tokens_limit": tokens_limit,
        "cost_today": cost,
        "cost_limit": cost_limit,
    }


def test_percentile_edge_cases():
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([3.0, 1.0], 50) == 1.0
    assert percentile([3.0, 1.0], 95) == 3.0
    assert percentile([float(i) for i in range(1, 21)], 95) == 19.0


def test_empty_input():
    m = compute_metrics(iter([]), _policy(), now=NOW)
    assert set(m) == CONTRACT_KEYS | EXTRA_KEYS
    assert m["totals"] == {"requests": 0, "allowed": 0, "redacted": 0, "blocked": 0, "flagged": 0}
    for key in (
        "blocks_by_check",
        "latency_ms_by_check",
        "blocks_by_checkpoint",
        "blocks_by_caller",
        "actions_by_check",
        "system_events",
        "policy_versions",
    ):
        assert m[key] == {}, key
    for key in ("overhead_ms", "rules_overhead_ms", "jev_latency_ms"):
        assert m[key] == ZERO, key
    assert m["timeline"] == [] and m["top_block_reasons"] == []
    assert m["tokens_total"] == 0 and m["cost_total"] == 0.0
    assert m["decided_by"] == {"rules": 0, "jev": 0, "fallback": 0}
    assert m["budget_by_caller"]["support"] == _budget(0, 50000, 0.0, 0.5)
    assert m["agents"] == {}
    zero_use = {"tokens_pct": 0.0, "cost_pct": 0.0}
    assert m["economics"] == {
        "cost_by_model": {},
        "cost_by_caller": {},
        "cost_by_day": {},
        "tokens_by_model": {},
        "cost_per_session": {"p50": 0.0, "p95": 0.0, "avg": 0.0},
        "cost_per_turn_avg": 0.0,
        "budget_utilization": {"demo": zero_use, "support": zero_use, "playground": zero_use},
        "blocked_before_upstream": 0,
        "price_per_1k_tokens": {"gemma4": 0.0},
    }
    assert json.loads(json.dumps(m)) == m


def test_hand_built_dataset_exact():
    m = compute_metrics(_dataset(), _policy(), now=NOW)
    assert set(m) == CONTRACT_KEYS | EXTRA_KEYS
    assert m["active_profile"] == "balanced"
    # r5 has only a system event, so it is not a pipeline request.
    assert m["totals"] == {"requests": 4, "allowed": 1, "redacted": 1, "blocked": 1, "flagged": 1}
    assert m["blocks_by_check"] == {"permissions": 1}
    assert m["latency_ms_by_check"] == {
        "jev": {"p50": 100.0, "p95": 200.0},
        "loop_detection": {"p50": 0.25, "p95": 0.25},
        "permissions": {"p50": 0.5, "p95": 0.5},
        "pii_secrets": {"p50": 0.75, "p95": 2.0},
        "signatures": {"p50": 1.0, "p95": 1.0},
    }
    # per request: r1 151.5, r2 3.0, r3 201.0, r4 1.25
    assert m["overhead_ms"] == {"p50": 3.0, "p95": 201.0}
    # without jev: r1 1.5, r2 3.0, r3 1.0, r4 1.25
    assert m["rules_overhead_ms"] == {"p50": 1.25, "p95": 3.0}
    assert m["jev_latency_ms"] == {"p50": 100.0, "p95": 200.0}
    assert m["budget_by_caller"] == {
        "demo": _budget(1600, 200000, 0.75, 1.0),
        "support": _budget(300, 50000, 0.0, 0.5),
        "playground": _budget(0, 200000, 0.0, 1.0),
    }
    assert m["enabled_checks"]["pii_secrets"] == {
        "input": "redact",
        "tool_call": "off",
        "tool_result": "redact",
        "output": "redact",
    }
    assert m["enabled_checks"]["jev"] == {
        "input": "block",
        "tool_call": "off",
        "tool_result": "block",
        "output": "monitor",
    }
    assert list(m["enabled_checks"]) == list(DEV_POLICY["checks"])
    assert m["blocks_by_checkpoint"] == {"tool_call": 1}
    assert m["blocks_by_caller"] == {"support": 1}
    assert m["actions_by_check"] == {
        "jev": {"allow": 3, "redact": 0, "block": 0, "flag": 0},
        "loop_detection": {"allow": 1, "redact": 0, "block": 0, "flag": 0},
        "permissions": {"allow": 2, "redact": 0, "block": 1, "flag": 0},
        "pii_secrets": {"allow": 3, "redact": 1, "block": 0, "flag": 0},
        "signatures": {"allow": 0, "redact": 0, "block": 0, "flag": 1},
    }
    assert m["decided_by"] == {"rules": 9, "jev": 2, "fallback": 1}
    assert m["system_events"] == {"auth_failed": 1, "policy_loaded": 2, "upstream_unavailable": 1}
    assert m["timeline"] == [
        {"minute": "2026-10-03T12:00:00Z", "requests": 2, "blocked": 0, "redacted": 1,
         "flagged": 0},
        {"minute": "2026-10-03T12:01:00Z", "requests": 2, "blocked": 1, "redacted": 0,
         "flagged": 1},
    ]  # fmt: skip
    assert m["top_block_reasons"] == [
        {"check": "permissions", "reason": "tool run_shell not allowed", "count": 1}
    ]
    assert m["tokens_total"] == 1900
    assert m["cost_total"] == 0.75
    assert m["policy_versions"] == {"0.1": 12, "0.2": 4}


def test_active_profile_overrides_enabled_checks():
    m = compute_metrics([], _policy(active_profile="strict"), now=NOW)
    assert m["active_profile"] == "strict"
    assert m["enabled_checks"]["pii_secrets"] == {
        "input": "block",
        "tool_call": "off",
        "tool_result": "block",
        "output": "block",
    }


def test_since_filters_everything_but_budget():
    m = compute_metrics(_dataset(), _policy(), since="2026-10-03T12:01:00Z", now=NOW)
    assert m["totals"] == {"requests": 2, "allowed": 0, "redacted": 0, "blocked": 1, "flagged": 1}
    assert m["system_events"] == {"policy_loaded": 1, "upstream_unavailable": 1}
    assert [b["minute"] for b in m["timeline"]] == ["2026-10-03T12:01:00Z"]
    assert m["tokens_total"] == 400
    assert m["policy_versions"] == {"0.1": 3, "0.2": 4}
    # Budget is "today" against a per-day limit, so the window does not shrink it.
    assert m["budget_by_caller"]["demo"]["tokens_today"] == 1600


def test_since_accepts_offset_form():
    m = compute_metrics(_dataset(), _policy(), since="2026-10-03T14:01:00+02:00", now=NOW)
    assert m["totals"]["requests"] == 2


def test_invalid_since_raises():
    with pytest.raises(ValueError):
        compute_metrics(_dataset(), _policy(), since="yesterday", now=NOW)


def test_budget_counts_today_only():
    records = [
        _req("old", "demo", "2026-10-02T23:59:59Z", "output", "pii_secrets", "allow",
             tokens=5000, cost=0.5),
        _req("new", "demo", "2026-10-03T00:00:01Z", "output", "pii_secrets", "allow",
             tokens=10, cost=0.25),
    ]  # fmt: skip
    m = compute_metrics(records, _policy(), now=NOW)
    assert m["budget_by_caller"]["demo"]["tokens_today"] == 10
    assert m["budget_by_caller"]["demo"]["cost_today"] == 0.25
    assert m["tokens_total"] == 5010
    # Same records seen from the next day: nothing is "today" any more.
    later = compute_metrics(records, _policy(), now=datetime(2026, 10, 4, 0, 0, 1, tzinfo=UTC))
    assert later["budget_by_caller"]["demo"]["tokens_today"] == 0


def test_unlimited_budget_reports_zero_limits():
    callers = {**DEV_POLICY["callers"]}
    callers["demo"] = {**callers["demo"], "budgets": {}}
    records = [
        _req("r", "demo", "2026-10-03T10:00:00Z", "output", "pii_secrets", "allow",
             tokens=42, cost=0.5),
    ]  # fmt: skip
    m = compute_metrics(records, _policy(callers=callers), now=NOW)
    assert m["budget_by_caller"]["demo"] == _budget(42, 0, 0.5, 0.0)


def test_system_events_excluded_from_check_stats():
    records = [
        _rec("2026-10-03T12:00:00Z", "auth_failed", "block", latency_ms=5.0, request_id="x"),
        _rec("2026-10-03T12:00:01Z", "bad_request", "block", latency_ms=3.0),
        _rec("2026-10-03T12:00:02Z", "policy_rejected", "block"),
        _rec("2026-10-03T12:00:03Z", "signature_feed_failed", "flag"),
        # Unknown event id without a checkpoint: cannot be a check result.
        _rec("2026-10-03T12:00:04Z", "something_new", "block"),
    ]
    m = compute_metrics(records, _policy(), now=NOW)
    assert m["totals"]["requests"] == 0
    assert m["blocks_by_check"] == {}
    assert m["latency_ms_by_check"] == {}
    assert m["actions_by_check"] == {}
    assert m["decided_by"] == {"rules": 0, "jev": 0, "fallback": 0}
    assert m["overhead_ms"] == ZERO and m["timeline"] == [] and m["top_block_reasons"] == []
    assert m["system_events"] == {
        "auth_failed": 1,
        "bad_request": 1,
        "policy_rejected": 1,
        "signature_feed_failed": 1,
        "something_new": 1,
    }


def test_timeline_ascending_regardless_of_input_order():
    records = list(reversed(_dataset()))
    records.append(
        _req("r0", "demo", "2026-10-03T09:15:59Z", "input", "permissions", "block", reason="x")
    )
    minutes = [b["minute"] for b in compute_metrics(records, _policy(), now=NOW)["timeline"]]
    assert minutes == ["2026-10-03T09:15:00Z", "2026-10-03T12:00:00Z", "2026-10-03T12:01:00Z"]


def test_top_block_reasons_ordered_by_count_then_check():
    records = [
        _req(f"t{i}", "demo", "2026-10-03T12:00:00Z", "tool_call", check, "block", reason=reason)
        for i, (check, reason) in enumerate(
            [("tool_args", "rm -rf"), ("tool_args", "rm -rf"), ("signatures", "sig-9"),
             ("permissions", "no tool")]
        )
    ]  # fmt: skip
    m = compute_metrics(records, _policy(), now=NOW)
    assert m["top_block_reasons"] == [
        {"check": "tool_args", "reason": "rm -rf", "count": 2},
        {"check": "permissions", "reason": "no tool", "count": 1},
        {"check": "signatures", "reason": "sig-9", "count": 1},
    ]


def test_result_is_json_serializable():
    m = compute_metrics(_dataset(), _policy(), now=NOW)
    assert json.loads(json.dumps(m)) == m


# --- turn_summary: agents and economics ---------------------------------------------------


def _summary(rid, caller, ts, checkpoint, action="allow", *, conv, overhead, model="gemma4", **kw):
    return _rec(
        ts, "turn_summary", action, request_id=rid, caller_id=caller, model=model,
        checkpoint=checkpoint, conversation_id=conv, latency_ms=overhead, overhead_ms=overhead,
        **kw,
    )  # fmt: skip


def test_turn_summary_rows_leave_the_check_stats_unchanged():
    day = "2026-10-03T"
    # The turn rows of the requests in _dataset(), carrying the same usage as its check rows.
    turns = [
        _summary("r1", "demo", day + "12:00:05.6Z", "input", conv="c1", overhead=100.5),
        _summary("r1", "demo", day + "12:00:06Z", "output", conv="c1", overhead=51.0,
                 tokens=1000, cost=0.5),
        _summary("r2", "demo", day + "12:00:40Z", "tool_result", "redact", conv="c1",
                 overhead=2.25),
        _summary("r2", "demo", day + "12:00:41Z", "output", conv="c1", overhead=0.75,
                 tokens=500, cost=0.25),
        _summary("r3", "support", day + "12:01:10Z", "input", conv="c2", overhead=200.5),
        _summary("r3", "support", day + "12:01:11Z", "tool_call", "block", conv="c2",
                 overhead=0.5, tokens=300, blocked_by="permissions"),
        _summary("r4", "demo", day + "12:01:30Z", "input", "flag", conv="c3", overhead=1.0,
                 policy_version="0.2"),
        _summary("r4", "demo", day + "12:01:31Z", "output", conv="c3", overhead=0.25,
                 tokens=100, policy_version="0.2"),
    ]  # fmt: skip
    base = compute_metrics(_dataset(), _policy(), now=NOW)
    both = compute_metrics(_dataset() + turns, _policy(), now=NOW)
    for key in set(base) - {"system_events", "policy_versions", "agents", "economics"}:
        assert both[key] == base[key], key
    assert both["system_events"] == {**base["system_events"], "turn_summary": 8}
    assert both["blocks_by_check"] == {"permissions": 1}
    assert "turn_summary" not in both["latency_ms_by_check"]
    assert "turn_summary" not in both["actions_by_check"]


def test_usage_on_a_check_row_and_its_turn_summary_counts_once():
    day = "2026-10-03T"
    records = [
        # An older-style check row with usage, and the turn_summary row with the same usage.
        _req("r", "demo", day + "12:00:00Z", "output", "pii_secrets", "allow",
             tokens=1000, cost=0.5),
        _summary("r", "demo", day + "12:00:00Z", "output", conv="c", overhead=1.0,
                 tokens=1000, cost=0.5),
    ]  # fmt: skip
    m = compute_metrics(records, _policy(), now=NOW)
    assert (m["tokens_total"], m["cost_total"]) == (1000, 0.5)
    assert m["budget_by_caller"]["demo"]["tokens_today"] == 1000
    assert m["budget_by_caller"]["demo"]["cost_today"] == 0.5
    assert (m["agents"]["demo"]["tokens"], m["agents"]["demo"]["cost"]) == (1000, 0.5)
    assert m["economics"]["cost_by_caller"] == {"demo": 0.5}
    assert m["economics"]["tokens_by_model"]["gemma4"]["total"] == 1000


def _agent_dataset() -> list[AuditRecord]:
    day = "2026-10-03T"
    return [
        # demo, session s1, three steps.
        _summary("t1", "demo", day + "12:00:00Z", "input", conv="s1", overhead=2.0, step=0),
        _summary("t1", "demo", day + "12:00:01Z", "tool_call", conv="s1", overhead=1.0, step=0,
                 tool_calls=2, tools="query_customers,run_shell", tokens=1000, prompt_tokens=800,
                 completion_tokens=200, cost=0.5, upstream_latency_ms=100.0),
        _summary("t2", "demo", day + "12:00:10Z", "tool_result", "redact", conv="s1",
                 overhead=3.0, step=1),
        _summary("t2", "demo", day + "12:00:11Z", "tool_call", "block", conv="s1", overhead=1.0,
                 step=1, tool_calls=1, tools="run_shell", tokens=2000, prompt_tokens=1500,
                 completion_tokens=500, cost=1.0, upstream_latency_ms=200.0,
                 blocked_by="tool_args"),
        _summary("t3", "demo", day + "12:00:20Z", "input", conv="s1", overhead=2.0, step=2),
        # The reply row carries a different id (redacted copy): the turn keeps its input row's.
        _summary("t3", "demo", day + "12:00:21Z", "output", conv="s1-redacted", overhead=1.0,
                 step=2, tool_calls=0, tokens=500, prompt_tokens=400, completion_tokens=100,
                 cost=0.25, upstream_latency_ms=90.0),
        # demo, session s2: blocked before the model was called.
        _summary("t4", "demo", day + "12:05:00Z", "input", "block", conv="s2", overhead=4.0,
                 blocked_by="signatures"),
        # support, session s3, yesterday, on another model.
        _summary("t5", "support", "2026-10-02T23:59:00Z", "input", "flag", conv="s3",
                 overhead=1.0, model="big"),
        _summary("t5", "support", "2026-10-02T23:59:01Z", "output", conv="s3", overhead=1.0,
                 model="big", tool_calls=0, tokens=1000, prompt_tokens=600,
                 completion_tokens=400, cost=2.0, upstream_latency_ms=48.0),
    ]  # fmt: skip


def _agent_policy() -> Policy:
    callers = {**DEV_POLICY["callers"]}
    callers["playground"] = {**callers["playground"], "budgets": {}}
    models = {
        "gemma4": {"upstream_base_url": "http://localhost:11434/v1", "price_per_1k_tokens": 0.5},
        "big": {"upstream_base_url": "http://big/v1", "price_per_1k_tokens": 2.0},
    }
    return _policy(callers=callers, models=models)


def test_agents_section_exact():
    m = compute_metrics(_agent_dataset(), _agent_policy(), now=NOW)
    assert m["agents"] == {
        "demo": {
            "sessions": 2,
            "turns": 4,
            "avg_steps_per_session": 2.0,
            "max_steps": 3,
            "tool_calls": 3,
            "tool_usage": {"query_customers": 1, "run_shell": 2},
            "blocked_turns": 2,
            "block_rate": 0.5,
            "redacted_turns": 0,
            "flagged_turns": 0,
            "tokens": 3500,
            "cost": 1.75,
            "avg_upstream_latency_ms": 130.0,
            # (3 + 4 + 3 + 4) / 4 turns
            "avg_overhead_ms": 3.5,
            # turns that reached the model: overhead 10 / (10 + upstream 390)
            "overhead_share": 0.025,
        },
        "support": {
            "sessions": 1,
            "turns": 1,
            "avg_steps_per_session": 1.0,
            "max_steps": 1,
            "tool_calls": 0,
            "tool_usage": {},
            "blocked_turns": 0,
            "block_rate": 0.0,
            "redacted_turns": 0,
            "flagged_turns": 1,
            "tokens": 1000,
            "cost": 2.0,
            "avg_upstream_latency_ms": 48.0,
            "avg_overhead_ms": 2.0,
            "overhead_share": 0.04,
        },
    }
    # Turn rows are system events: no pipeline request, no per-check stats.
    assert m["totals"]["requests"] == 0
    assert m["actions_by_check"] == {} and m["latency_ms_by_check"] == {}
    assert m["system_events"] == {"turn_summary": 9}
    assert (m["tokens_total"], m["cost_total"]) == (4500, 3.75)


def test_economics_section_exact():
    m = compute_metrics(_agent_dataset(), _agent_policy(), now=NOW)
    assert m["economics"] == {
        "cost_by_model": {"big": 2.0, "gemma4": 1.75},
        "cost_by_caller": {"demo": 1.75, "support": 2.0},
        "cost_by_day": {"2026-10-02": 2.0, "2026-10-03": 1.75},
        "tokens_by_model": {
            "big": {"prompt": 600, "completion": 400, "total": 1000},
            "gemma4": {"prompt": 2700, "completion": 800, "total": 3500},
        },
        # sessions s2 0.0, s1 1.75, s3 2.0
        "cost_per_session": {"p50": 1.75, "p95": 2.0, "avg": 1.25},
        "cost_per_turn_avg": 0.75,
        # Today only: demo used 3500 of 200000 tokens and 1.75 of 1.0 cost; support's use was
        # yesterday; playground has no limits.
        "budget_utilization": {
            "demo": {"tokens_pct": 1.75, "cost_pct": 175.0},
            "support": {"tokens_pct": 0.0, "cost_pct": 0.0},
            "playground": {"tokens_pct": None, "cost_pct": None},
        },
        "blocked_before_upstream": 1,
        "price_per_1k_tokens": {"big": 2.0, "gemma4": 0.5},
    }
    assert json.loads(json.dumps(m)) == m


def test_agents_and_economics_follow_since():
    m = compute_metrics(_agent_dataset(), _agent_policy(), since="2026-10-03T12:00:15Z", now=NOW)
    assert list(m["agents"]) == ["demo"]
    assert (m["agents"]["demo"]["turns"], m["agents"]["demo"]["sessions"]) == (2, 2)
    assert m["economics"]["cost_by_day"] == {"2026-10-03": 0.25}
    assert m["economics"]["blocked_before_upstream"] == 1
    # Budget utilisation is per day, not per window.
    assert m["economics"]["budget_utilization"]["demo"]["tokens_pct"] == 1.75
