import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.audit import MemoryAuditSink
from app.budget import UsageLedger
from app.checks.base import CheckContext, Signature, make_result
from app.checks.jev import CHECK as JEV
from app.models import (
    Action,
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    Message,
    Mode,
    Redaction,
    ToolCall,
    Usage,
)
from app.pipeline import (
    apply_mode,
    apply_redactions,
    build_context,
    conversation_id,
    detect_reply_checkpoint,
    detect_request_checkpoint,
    run_checkpoint,
)
from app.policy import CheckConfig, Policy
from tests.conftest import FakeJudge

ALL = frozenset(Checkpoint)


class FakeCheck:
    """Returns a fixed verdict and records what it saw. Optionally raises or sleeps."""

    def __init__(
        self,
        id: str,
        rank: int,
        verdict: str = "allow",
        *,
        checkpoints: frozenset[Checkpoint] = ALL,
        redactions: list[Redaction] | None = None,
        raises: Exception | None = None,
        sleep_s: float = 0.0,
    ) -> None:
        self.id = id
        self.cost_rank = rank
        self.checkpoints = checkpoints
        self.verdict = verdict
        self.redactions = redactions
        self.raises = raises
        self.sleep_s = sleep_s
        self.seen: list[CanonicalRequest] = []
        self.settings: dict[str, Any] | None = None

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        started = time.perf_counter()
        self.seen.append(request)
        self.settings = settings
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        if self.raises:
            raise self.raises
        return make_result(
            self.id,
            request,
            self.verdict,
            f"{self.id} says {self.verdict}",
            started,
            redactions=self.redactions,
        )


def _policy(checks: dict[str, Any], version: str = "t1", price: float = 0.0) -> Policy:
    policy = Policy.model_validate(
        {
            "version": version,
            "active_profile": "balanced",
            "profiles": {"balanced": {"jev_threshold": 0.6}},
            "models": {"m": {"upstream_base_url": "http://x/v1", "price_per_1k_tokens": price}},
            "callers": {
                "demo": {
                    "api_key_env": "DEMO_API_KEY",
                    "role": "developer",
                    "allowed_models": ["m"],
                    "allowed_tools": ["run_shell"],
                    "budgets": {"requests_per_minute": 5, "tokens_per_day": 100},
                }
            },
            "jev": {"fallback": {"model": "m", "base_url": "http://x/v1"}},
        }
    )
    # The fake checks have ids the policy schema rightly rejects, so their sections are set
    # after validation. The schema is tested in test_policy_schema.py.
    sections = {cid: CheckConfig.model_validate(raw) for cid, raw in checks.items()}
    return policy.model_copy(update={"checks": sections})


def _request(
    checkpoint: Checkpoint = Checkpoint.input,
    content: str = "hello",
    reply: Message | None = None,
) -> CanonicalRequest:
    return CanonicalRequest(
        request_id="req-1",
        caller_id="demo",
        model="m",
        checkpoint=checkpoint,
        messages=[Message(role="user", content=content)],
        reply=reply,
    )


async def _run(checks, modes: dict[str, Any], request=None, audit_sink=None, **kw):
    policy = _policy(modes)
    return await run_checkpoint(
        request or _request(),
        policy,
        policy.version,
        policy.callers["demo"],
        audit=audit_sink if audit_sink is not None else MemoryAuditSink(),
        checks=checks,
        **kw,
    )


# --- checkpoint detection ---------------------------------------------------------------


def test_detect_request_checkpoint():
    user = Message(role="user", content="q")
    tool = Message(role="tool", content="r", tool_call_id="1")
    assert detect_request_checkpoint([user]) == Checkpoint.input
    assert detect_request_checkpoint([user, tool]) == Checkpoint.tool_result
    assert detect_request_checkpoint([tool, user]) == Checkpoint.input
    assert detect_request_checkpoint([]) == Checkpoint.input


def test_detect_reply_checkpoint():
    call = ToolCall(id="1", name="run_shell", arguments={"cmd": "ls"})
    assert detect_reply_checkpoint(Message(role="assistant", tool_calls=[call])) == (
        Checkpoint.tool_call
    )
    assert detect_reply_checkpoint(Message(role="assistant", content="hi")) == Checkpoint.output


# --- mode table -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "mode", "expected"),
    [
        ("allow", Mode.monitor, Action.allow),
        ("allow", Mode.redact, Action.allow),
        ("allow", Mode.block, Action.allow),
        ("redact", Mode.monitor, Action.flag),
        ("redact", Mode.redact, Action.redact),
        ("redact", Mode.block, Action.block),
        ("block", Mode.monitor, Action.flag),
        ("block", Mode.redact, Action.block),
        ("block", Mode.block, Action.block),
        ("error", Mode.monitor, Action.flag),
        ("error", Mode.redact, Action.block),
        ("error", Mode.block, Action.block),
    ],
)
def test_mode_table(verdict, mode, expected):
    assert apply_mode(verdict, mode) == expected


def test_mode_off_is_never_applied():
    with pytest.raises(ValueError):
        apply_mode("block", Mode.off)


# --- ordering, stopping, skipping -------------------------------------------------------


async def test_checks_run_in_cost_order():
    checks = [FakeCheck("c", 3), FakeCheck("a", 1), FakeCheck("b", 2)]
    modes = {cid: {"input": "block"} for cid in "abc"}
    decision, _ = await _run(checks, modes)
    assert [r.check for r in decision.results] == ["a", "b", "c"]
    assert decision.action == Action.allow
    assert decision.blocked_by is None


async def test_first_block_stops_later_checks():
    first = FakeCheck("first", 1, "block")
    later = FakeCheck("later", 2)
    decision, _ = await _run(
        [later, first], {"first": {"input": "block"}, "later": {"input": "block"}}
    )
    assert decision.action == Action.block
    assert decision.blocked_by == "first"
    assert [r.check for r in decision.results] == ["first"]
    assert later.seen == []


async def test_monitor_block_does_not_stop_the_pipeline():
    first = FakeCheck("first", 1, "block")
    later = FakeCheck("later", 2)
    decision, _ = await _run(
        [first, later], {"first": {"input": "monitor"}, "later": {"input": "block"}}
    )
    assert [r.action for r in decision.results] == [Action.flag, Action.allow]
    assert decision.action == Action.flag
    assert decision.blocked_by is None


async def test_off_and_not_applicable_checks_are_skipped():
    off = FakeCheck("off", 1, "block")
    unconfigured = FakeCheck("unconfigured", 2, "block")
    elsewhere = FakeCheck("elsewhere", 3, "block", checkpoints=frozenset({Checkpoint.output}))
    runs = FakeCheck("runs", 4)
    modes = {"off": {"input": "off"}, "elsewhere": {"input": "block"}, "runs": {"input": "block"}}
    decision, _ = await _run([off, unconfigured, elsewhere, runs], modes)
    assert [r.check for r in decision.results] == ["runs"]
    assert off.seen == unconfigured.seen == elsewhere.seen == []


async def test_settings_are_the_check_params_without_modes():
    check = FakeCheck("a", 1)
    await _run([check], {"a": {"input": "block", "limit": 3, "types": ["email"]}})
    assert check.settings == {"limit": 3, "types": ["email"]}


# --- errors and timeouts ----------------------------------------------------------------


@pytest.mark.parametrize(("mode", "expected"), [("block", Action.block), ("monitor", Action.flag)])
async def test_exception_becomes_error(mode, expected):
    boom = FakeCheck("boom", 1, raises=RuntimeError("feed exploded"))
    decision, _ = await _run([boom], {"boom": {"input": mode}})
    (result,) = decision.results
    assert result.verdict == "error"
    assert result.action == expected
    assert "feed exploded" in result.reason
    assert decision.action == expected


async def test_timeout_becomes_error():
    slow = FakeCheck("slow", 1, sleep_s=1.0)
    decision, _ = await _run([slow], {"slow": {"input": "block", "timeout_s": 0.01}})
    (result,) = decision.results
    assert result.verdict == "error"
    assert result.action == Action.block
    assert "timed out" in result.reason
    assert decision.blocked_by == "slow"


async def test_invalid_redaction_fails_closed():
    bad = [Redaction(kind="X", start=0, end=99, replacement="[X]", message_index=0)]
    check = FakeCheck("bad", 1, "redact", redactions=bad)
    decision, out = await _run([check], {"bad": {"input": "redact"}})
    assert decision.results[0].verdict == "error"
    assert decision.action == Action.block
    assert out.messages[0].content == "hello"


# --- redactions -------------------------------------------------------------------------


def _red(start: int, end: int, kind: str = "X", index: int = 0) -> Redaction:
    return Redaction(
        kind=kind, start=start, end=end, replacement=f"[REDACTED:{kind}]", message_index=index
    )


async def test_redaction_is_seen_by_later_checks_and_returned():
    original = _request(content="my ssn is 123-45-6789 ok")
    redactor = FakeCheck("redactor", 1, "redact", redactions=[_red(10, 21, "SSN")])
    judge_like = FakeCheck("judge_like", 2)
    modes = {"redactor": {"input": "redact"}, "judge_like": {"input": "block"}}
    decision, out = await _run([redactor, judge_like], modes, request=original)
    assert judge_like.seen[0].messages[0].content == "my ssn is [REDACTED:SSN] ok"
    assert out.messages[0].content == "my ssn is [REDACTED:SSN] ok"
    assert original.messages[0].content == "my ssn is 123-45-6789 ok"
    assert decision.action == Action.redact


async def test_monitor_redaction_reaches_later_checks_but_not_upstream():
    # Monitor must not change the request, but a later check (Jev) must still never see the
    # sensitive text (constitution V).
    redactor = FakeCheck("redactor", 1, "redact", redactions=[_red(0, 5)])
    later = FakeCheck("later", 2)
    modes = {"redactor": {"input": "monitor"}, "later": {"input": "block"}}
    decision, out = await _run([redactor, later], modes)
    assert decision.results[0].action == Action.flag
    assert later.seen[0].messages[0].content == "[REDACTED:X]"
    assert out.messages[0].content == "hello"


async def test_enforced_redaction_after_a_monitor_redaction():
    # SSN is only monitored; EMAIL is enforced. The EMAIL span is an offset into the text the
    # check saw, which already carries the monitor redaction, so it cannot be mapped back onto
    # the raw text: the message it touches is forwarded as checked (more redacted, never less).
    msgs = [
        Message(role="user", content="id 123-45-6789"),
        Message(role="user", content="id 123-45-6789 at a@b.co"),
    ]
    request = CanonicalRequest(
        request_id="req-1", caller_id="demo", model="m", checkpoint=Checkpoint.input, messages=msgs
    )
    monitored = FakeCheck(
        "monitored", 1, "redact", redactions=[_red(3, 14, "SSN", 0), _red(3, 14, "SSN", 1)]
    )
    # "id [REDACTED:SSN] at " is 21 characters long.
    enforced = FakeCheck("enforced", 2, "redact", redactions=[_red(21, 27, "EMAIL", 1)])
    later = FakeCheck("later", 3)
    modes = {
        "monitored": {"input": "monitor"},
        "enforced": {"input": "redact"},
        "later": {"input": "block"},
    }

    decision, out = await _run([monitored, enforced, later], modes, request=request)

    assert [r.action for r in decision.results] == [Action.flag, Action.redact, Action.allow]
    assert [m.content for m in later.seen[0].messages] == [
        "id [REDACTED:SSN]",
        "id [REDACTED:SSN] at [REDACTED:EMAIL]",
    ]
    assert [m.content for m in out.messages] == [
        "id 123-45-6789",
        "id [REDACTED:SSN] at [REDACTED:EMAIL]",
    ]
    assert [m.content for m in request.messages] == ["id 123-45-6789", "id 123-45-6789 at a@b.co"]


async def test_invalid_redaction_in_monitor_mode_is_an_error_flag():
    bad = [Redaction(kind="X", start=0, end=99, replacement="[X]", message_index=0)]
    check = FakeCheck("bad", 1, "redact", redactions=bad)
    later = FakeCheck("later", 2)
    decision, out = await _run(
        [check, later], {"bad": {"input": "monitor"}, "later": {"input": "block"}}
    )
    assert (decision.results[0].verdict, decision.results[0].action) == ("error", Action.flag)
    assert decision.results[0].redactions == []
    assert later.seen[0].messages[0].content == "hello"
    assert out.messages[0].content == "hello"


def test_apply_redactions_right_to_left_and_reply():
    req = _request(content="a@b.c and 555", reply=Message(role="assistant", content="key sk-1"))
    out = apply_redactions(req, [_red(0, 5, "EMAIL"), _red(10, 13, "PHONE"), _red(4, 8, "K", -1)])
    assert out.messages[0].content == "[REDACTED:EMAIL] and [REDACTED:PHONE]"
    assert out.reply.content == "key [REDACTED:K]"
    assert req.messages[0].content == "a@b.c and 555"


@pytest.mark.parametrize(
    ("spans", "expected"),
    [
        # partial overlap: both kinds kept, nothing of 2..8 survives
        ([(2, 6, "A"), (4, 8, "B")], "01[REDACTED:A][REDACTED:B]89"),
        # containment: the inner span must not resurrect text of the outer one
        ([(1, 9, "A"), (3, 5, "B")], "0[REDACTED:A][REDACTED:B]9"),
        # same span found by two detectors of the same kind: one replacement
        ([(2, 5, "A"), (2, 5, "A")], "01[REDACTED:A]56789"),
        # adjacent spans are not merged
        ([(0, 2, "A"), (2, 4, "B")], "[REDACTED:A][REDACTED:B]456789"),
    ],
)
def test_apply_redactions_overlapping(spans, expected):
    req = _request(content="0123456789")
    out = apply_redactions(req, [_red(s, e, k) for s, e, k in spans])
    assert out.messages[0].content == expected


def test_apply_redactions_rejects_bad_targets():
    with pytest.raises(ValueError):
        apply_redactions(_request(), [_red(0, 1, index=5)])
    with pytest.raises(ValueError):
        apply_redactions(_request(), [_red(0, 1, index=-1)])  # no reply


# --- decision and audit -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ([], Action.allow),
        ([("allow", "block")], Action.allow),
        ([("allow", "block"), ("block", "monitor")], Action.flag),
        ([("block", "monitor"), ("redact", "redact"), ("allow", "block")], Action.redact),
        ([("redact", "redact"), ("block", "block")], Action.block),
    ],
)
async def test_decision_takes_the_strongest_action(verdicts, expected):
    checks, modes = [], {}
    for i, (verdict, mode) in enumerate(verdicts):
        redactions = [_red(0, 1)] if verdict == "redact" else None
        checks.append(FakeCheck(f"c{i}", i, verdict, redactions=redactions))
        modes[f"c{i}"] = {"input": mode}
    decision, _ = await _run(checks, modes)
    assert decision.action == expected
    assert decision.checkpoint == Checkpoint.input
    assert decision.request_id == "req-1"


async def test_one_audit_record_per_result(audit_sink):
    checks = [FakeCheck("a", 1), FakeCheck("b", 2, "block"), FakeCheck("c", 3)]
    modes = {cid: {"input": "block"} for cid in "abc"}
    decision, _ = await _run(checks, modes, audit_sink=audit_sink)
    records = audit_sink.records
    assert [r.check for r in decision.results] == ["a", "b"]
    assert [r.check for r in records] == ["a", "b", "turn_summary"]
    rec = records[1]
    assert datetime.fromisoformat(rec.ts).utcoffset() == UTC.utcoffset(None)
    assert (rec.request_id, rec.caller_id, rec.model) == ("req-1", "demo", "m")
    assert rec.checkpoint == Checkpoint.input
    assert (rec.action, rec.reason, rec.score) == (Action.block, "b says block", 1.0)
    assert rec.latency_ms == decision.results[1].latency_ms
    assert rec.decided_by == DecidedBy.rules
    assert rec.policy_version == "t1"
    assert (rec.tokens, rec.cost) == (0, 0.0)


@pytest.mark.parametrize(
    ("checkpoint", "with_usage"),
    [
        (Checkpoint.input, False),
        (Checkpoint.tool_result, False),
        (Checkpoint.tool_call, True),
        (Checkpoint.output, True),
    ],
)
async def test_usage_only_on_first_reply_record(checkpoint, with_usage):
    reply = None
    if checkpoint == Checkpoint.tool_call:
        reply = Message(role="assistant", tool_calls=[ToolCall(id="1", name="run_shell")])
    elif checkpoint == Checkpoint.output:
        reply = Message(role="assistant", content="done")
    sink = MemoryAuditSink()
    modes = {cid: {checkpoint.value: "block"} for cid in "ab"}
    await _run(
        [FakeCheck("a", 1), FakeCheck("b", 2)],
        modes,
        request=_request(checkpoint, reply=reply),
        audit_sink=sink,
        usage_tokens=1200,
        usage_cost=0.36,
    )
    # Usage sits on the turn_summary row only, so summing the audit table counts it once.
    usage = [(r.tokens, r.cost) for r in sink.records]
    assert usage == [(0, 0.0), (0, 0.0), (1200, 0.36) if with_usage else (0, 0.0)]


# --- turn_summary -----------------------------------------------------------------------

SYSTEM = Message(role="system", content="you are a support agent")
USER = Message(role="user", content="list the customers in Berlin")
CALLS = [
    ToolCall(id="c1", name="query_customers", arguments={"city": "Berlin"}),
    ToolCall(id="c2", name="run_shell", arguments={"cmd": "ls"}),
]
STEP0 = [SYSTEM, USER]
STEP1 = [
    SYSTEM,
    USER,
    Message(role="assistant", tool_calls=CALLS),
    Message(role="tool", content="3 rows", tool_call_id="c1"),
    Message(role="tool", content="a.txt", tool_call_id="c2"),
]
# One agent session: the conversation and reply the pipeline sees at each checkpoint.
SESSION = {
    Checkpoint.input: (STEP0, None),
    Checkpoint.tool_call: (STEP0, Message(role="assistant", tool_calls=CALLS)),
    Checkpoint.tool_result: (STEP1, None),
    Checkpoint.output: (STEP1, Message(role="assistant", content="3 customers")),
}


def _session_request(checkpoint: Checkpoint) -> CanonicalRequest:
    messages, reply = SESSION[checkpoint]
    return CanonicalRequest(
        request_id="req-1",
        caller_id="demo",
        model="m",
        checkpoint=checkpoint,
        messages=messages,
        reply=reply,
    )


@pytest.mark.parametrize(
    ("checkpoint", "step", "messages", "tool_calls", "tools", "total", "with_usage"),
    [
        (Checkpoint.input, 0, 2, None, None, 0, False),
        (Checkpoint.tool_call, 0, 2, 2, "query_customers,run_shell", 2, True),
        (Checkpoint.tool_result, 1, 5, None, None, 2, False),
        (Checkpoint.output, 1, 5, 0, None, 2, True),
    ],
)
async def test_turn_summary_row_at_each_checkpoint(
    checkpoint, step, messages, tool_calls, tools, total, with_usage
):
    sink = MemoryAuditSink()
    modes = {cid: {checkpoint.value: "block"} for cid in "ab"}
    decision, _ = await _run(
        [FakeCheck("a", 1), FakeCheck("b", 2)],
        modes,
        request=_session_request(checkpoint),
        audit_sink=sink,
        usage=Usage(prompt_tokens=900, completion_tokens=300, upstream_latency_ms=250.0),
        usage_cost=0.36,
    )
    assert [r.check for r in sink.records] == ["a", "b", "turn_summary"]
    summary = sink.records[-1]
    overhead = sum(r.latency_ms for r in decision.results)
    assert datetime.fromisoformat(summary.ts).utcoffset() == UTC.utcoffset(None)
    assert summary.model_dump(exclude={"id", "ts"}) == {
        "request_id": "req-1",
        "caller_id": "demo",
        "model": "m",
        "checkpoint": checkpoint,
        "check": "turn_summary",
        "action": Action.allow,
        "reason": "allowed",
        "score": 0.0,
        "latency_ms": overhead,
        "decided_by": DecidedBy.rules,
        "tokens": 1200 if with_usage else 0,
        "cost": 0.36 if with_usage else 0.0,
        "policy_version": "t1",
        # The same id at every checkpoint of both steps: the session is recognised.
        "conversation_id": conversation_id("demo", STEP0),
        "step": step,
        "messages": messages,
        "tool_calls": tool_calls,
        "tools": tools,
        "tool_calls_total": total,
        "prompt_tokens": 900 if with_usage else None,
        "completion_tokens": 300 if with_usage else None,
        "upstream_latency_ms": 250.0 if with_usage else None,
        "overhead_ms": overhead,
        "blocked_by": None,
    }


async def test_turn_summary_is_written_when_every_check_is_off():
    sink = MemoryAuditSink()
    off = FakeCheck("off", 1, "block")
    decision, _ = await _run(
        [off],
        {"off": {"output": "off"}},
        request=_session_request(Checkpoint.output),
        audit_sink=sink,
        usage=Usage(prompt_tokens=100, completion_tokens=20),
    )
    assert decision.results == [] and decision.action == Action.allow
    [summary] = sink.records
    assert summary.check == "turn_summary"
    assert summary.action == Action.allow
    assert summary.reason == "allowed: no check enabled at this checkpoint"
    assert (summary.score, summary.latency_ms, summary.overhead_ms) == (0.0, 0.0, 0.0)
    # The upstream usage is kept even though no check row was written.
    assert (summary.tokens, summary.prompt_tokens, summary.completion_tokens) == (120, 100, 20)
    assert summary.upstream_latency_ms is None


async def test_turn_summary_of_a_rule_block():
    sink = MemoryAuditSink()
    checks = [
        FakeCheck("a", 1, "redact", redactions=[_red(0, 1)]),
        FakeCheck("tool_args", 2, "block"),
    ]
    await _run(checks, {"a": {"input": "redact"}, "tool_args": {"input": "block"}}, audit_sink=sink)
    summary = sink.records[-1]
    assert (summary.action, summary.reason) == (Action.block, "blocked by tool_args")
    assert (summary.blocked_by, summary.decided_by, summary.score) == (
        "tool_args",
        DecidedBy.rules,
        1.0,
    )


@pytest.mark.parametrize("decided_by", ["jev", "fallback"])
async def test_turn_summary_credits_jev_for_its_block(decided_by):
    sink = MemoryAuditSink()
    await _run(
        [JEV],
        {"jev": {"input": "block"}},
        audit_sink=sink,
        judge=FakeJudge(score=0.9, decided_by=decided_by),
    )
    summary = sink.records[-1]
    assert (summary.reason, summary.blocked_by) == ("blocked by jev", "jev")
    assert (summary.decided_by, summary.score) == (DecidedBy(decided_by), 0.9)


async def test_turn_summary_reason_names_redacting_and_flagging_checks():
    sink = MemoryAuditSink()
    checks = [
        FakeCheck("r1", 1, "redact", redactions=[_red(0, 1)]),
        FakeCheck("r2", 2, "redact", redactions=[_red(2, 3)]),
        FakeCheck("m", 3, "block"),
    ]
    modes = {"r1": {"input": "redact"}, "r2": {"input": "redact"}, "m": {"input": "monitor"}}
    await _run(checks, modes, audit_sink=sink)
    redacted = sink.records[-1]
    assert (redacted.action, redacted.reason) == (Action.redact, "redacted by r1, r2")

    await _run([FakeCheck("m", 1, "block")], {"m": {"input": "monitor"}}, audit_sink=sink)
    flagged = sink.records[-1]
    assert (flagged.action, flagged.reason) == (Action.flag, "flagged by m")
    assert flagged.decided_by == DecidedBy.rules


def test_conversation_id_is_stable_across_steps_and_differs_by_caller():
    ids = {conversation_id("demo", messages) for messages, _ in SESSION.values()}
    assert len(ids) == 1
    [cid] = ids
    assert re.fullmatch(r"[0-9a-f]{16}", cid)
    assert conversation_id("support", STEP0) != cid
    assert conversation_id("demo", [SYSTEM, Message(role="user", content="other task")]) != cid
    # The system prompt is not part of the id; only the caller and the first user message are.
    assert conversation_id("demo", [USER]) == cid


async def test_cost_is_computed_from_the_policy_price():
    policy = _policy({"a": {"output": "block"}}, price=0.25)

    async def summary(**usage_kw: Any):
        sink = MemoryAuditSink()
        await run_checkpoint(
            _session_request(Checkpoint.output),
            policy,
            policy.version,
            policy.callers["demo"],
            audit=sink,
            checks=[FakeCheck("a", 1)],
            **usage_kw,
        )
        return sink.records[-1]

    # 2000 tokens at 0.25 per 1k.
    row = await summary(usage=Usage(prompt_tokens=1500, completion_tokens=500))
    assert (row.tokens, row.cost) == (2000, 0.5)
    # The older usage_tokens argument is priced the same way.
    row = await summary(usage_tokens=4000)
    assert (row.tokens, row.cost, row.prompt_tokens) == (4000, 1.0, None)
    # An explicit cost wins, even zero.
    row = await summary(usage=Usage(prompt_tokens=1500, completion_tokens=500), usage_cost=0.0)
    assert (row.tokens, row.cost) == (2000, 0.0)


# --- context ----------------------------------------------------------------------------


def test_build_context_from_policy_and_caller():
    policy = _policy({})
    ledger = UsageLedger()
    judge = FakeJudge()
    sigs = [Signature(id="s1", pattern=re.compile("x"))]
    ctx = build_context(policy, policy.callers["demo"], ledger=ledger, signatures=sigs, judge=judge)
    assert ctx.caller_role == "developer"
    assert ctx.allowed_models == ["m"]
    assert ctx.allowed_tools == ["run_shell"]
    assert (ctx.requests_per_minute, ctx.tokens_per_day, ctx.cost_per_day) == (5, 100, None)
    assert ctx.ledger is ledger
    assert ctx.signatures is sigs
    assert ctx.jev_threshold == 0.6
    assert ctx.judge is judge
