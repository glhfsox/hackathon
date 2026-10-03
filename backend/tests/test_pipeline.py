import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from app.checks.base import CheckContext, Signature, make_result
from app.checks.jev import CHECK as JEV
from app.core.budget import UsageLedger
from app.core.pipeline import (
    apply_redactions,
    build_context,
    conversation_id,
    detect_reply_checkpoint,
    detect_request_checkpoint,
    run_checkpoint,
    to_action,
)
from app.models import (
    Action,
    CanonicalRequest,
    Checkpoint,
    CheckResult,
    DecidedBy,
    Message,
    Redaction,
    ToolCall,
    Usage,
)
from app.models.policy import CheckSection, Policy
from app.observability.sinks import MemoryAuditSink
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
            "jev_threshold": 0.6,
            "models": {"m": {"upstream_base_url": "http://x/v1", "price_per_1k_tokens": price}},
            "jev": {"fallback": {"model": "m", "base_url": "http://x/v1"}},
        }
    )
    # The fake checks have ids the policy schema rightly rejects, so their sections are set
    # after validation. The schema is tested in test_policy_schema.py.
    sections = {cid: CheckSection.model_validate(raw) for cid, raw in checks.items()}
    return policy.model_copy(update={"checks": sections})


def _request(
    checkpoint: Checkpoint = Checkpoint.INPUT,
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


async def _run(checks, sections: dict[str, Any], request=None, audit_sink=None, **kw):
    """Run `checks` with the policy sections in `sections`: a check without one is off."""
    policy = _policy(sections)
    return await run_checkpoint(
        request or _request(),
        policy,
        policy.version,
        audit=audit_sink if audit_sink is not None else MemoryAuditSink(),
        checks=checks,
        **kw,
    )


# --- checkpoint detection ---------------------------------------------------------------


def test_detect_request_checkpoint():
    user = Message(role="user", content="q")
    tool = Message(role="tool", content="r", tool_call_id="1")
    assert detect_request_checkpoint([user]) == Checkpoint.INPUT
    assert detect_request_checkpoint([user, tool]) == Checkpoint.TOOL_RESULT
    assert detect_request_checkpoint([tool, user]) == Checkpoint.INPUT
    assert detect_request_checkpoint([]) == Checkpoint.INPUT


def test_detect_reply_checkpoint():
    call = ToolCall(id="1", name="run_shell", arguments={"cmd": "ls"})
    assert detect_reply_checkpoint(Message(role="assistant", tool_calls=[call])) == (
        Checkpoint.TOOL_CALL
    )
    assert detect_reply_checkpoint(Message(role="assistant", content="hi")) == Checkpoint.OUTPUT


# --- verdict to action -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("allow", Action.ALLOW),
        ("redact", Action.REDACT),
        ("block", Action.BLOCK),
        ("error", Action.BLOCK),  # never allow on an error
    ],
)
def test_verdict_to_action(verdict, expected):
    assert to_action(verdict) == expected


# --- ordering, stopping, skipping -------------------------------------------------------


async def test_checks_run_in_cost_order():
    checks = [FakeCheck("c", 3), FakeCheck("a", 1), FakeCheck("b", 2)]
    sections = {cid: {} for cid in "abc"}
    decision, _ = await _run(checks, sections)
    assert [r.check for r in decision.results] == ["a", "b", "c"]
    assert decision.action == Action.ALLOW
    assert decision.blocked_by is None


async def test_first_block_stops_later_checks():
    first = FakeCheck("first", 1, "block")
    later = FakeCheck("later", 2)
    decision, _ = await _run([later, first], {"first": {}, "later": {}})
    assert decision.action == Action.BLOCK
    assert decision.blocked_by == "first"
    assert [r.check for r in decision.results] == ["first"]
    assert later.seen == []


async def test_off_and_not_applicable_checks_are_skipped():
    # A check without a policy section is off; an enabled one runs only where it applies.
    off = FakeCheck("off", 1, "block")
    elsewhere = FakeCheck("elsewhere", 2, "block", checkpoints=frozenset({Checkpoint.OUTPUT}))
    runs = FakeCheck("runs", 3)
    decision, _ = await _run([off, elsewhere, runs], {"elsewhere": {}, "runs": {}})
    assert [r.check for r in decision.results] == ["runs"]
    assert off.seen == elsewhere.seen == []


async def test_settings_are_the_check_params():
    check = FakeCheck("a", 1)
    await _run([check], {"a": {"limit": 3, "types": ["email"]}})
    assert check.settings == {"limit": 3, "types": ["email"]}


# --- errors and timeouts ----------------------------------------------------------------


async def test_exception_becomes_error_and_blocks():
    boom = FakeCheck("boom", 1, raises=RuntimeError("feed exploded"))
    decision, _ = await _run([boom], {"boom": {}})
    (result,) = decision.results
    assert result.verdict == "error"
    assert result.action == Action.BLOCK
    assert "feed exploded" in result.reason
    assert decision.blocked_by == "boom"


async def test_timeout_becomes_error():
    slow = FakeCheck("slow", 1, sleep_s=1.0)
    decision, _ = await _run([slow], {"slow": {"timeout_s": 0.01}})
    (result,) = decision.results
    assert result.verdict == "error"
    assert result.action == Action.BLOCK
    assert "timed out" in result.reason
    assert decision.blocked_by == "slow"


async def test_invalid_redaction_fails_closed():
    bad = [Redaction(kind="X", start=0, end=99, replacement="[X]", message_index=0)]
    check = FakeCheck("bad", 1, "redact", redactions=bad)
    decision, out = await _run([check], {"bad": {}})
    assert decision.results[0].verdict == "error"
    assert decision.action == Action.BLOCK
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
    sections = {"redactor": {}, "judge_like": {}}
    decision, out = await _run([redactor, judge_like], sections, request=original)
    assert judge_like.seen[0].messages[0].content == "my ssn is [REDACTED:SSN] ok"
    assert out.messages[0].content == "my ssn is [REDACTED:SSN] ok"
    assert original.messages[0].content == "my ssn is 123-45-6789 ok"
    assert decision.action == Action.REDACT


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
        ([], Action.ALLOW),
        (["allow"], Action.ALLOW),
        (["allow", "redact", "allow"], Action.REDACT),
        (["redact", "block"], Action.BLOCK),
    ],
)
async def test_decision_takes_the_strongest_action(verdicts, expected):
    checks, sections = [], {}
    for i, verdict in enumerate(verdicts):
        redactions = [_red(0, 1)] if verdict == "redact" else None
        checks.append(FakeCheck(f"c{i}", i, verdict, redactions=redactions))
        sections[f"c{i}"] = {}
    decision, _ = await _run(checks, sections)
    assert decision.action == expected
    assert decision.checkpoint == Checkpoint.INPUT
    assert decision.request_id == "req-1"


async def test_one_audit_record_per_result(audit_sink):
    checks = [FakeCheck("a", 1), FakeCheck("b", 2, "block"), FakeCheck("c", 3)]
    sections = {cid: {} for cid in "abc"}
    decision, _ = await _run(checks, sections, audit_sink=audit_sink)
    records = audit_sink.records
    assert [r.check for r in decision.results] == ["a", "b"]
    assert [r.check for r in records] == ["a", "b", "turn_summary"]
    rec = records[1]
    assert datetime.fromisoformat(rec.ts).utcoffset() == UTC.utcoffset(None)
    assert (rec.request_id, rec.caller_id, rec.model) == ("req-1", "demo", "m")
    assert rec.checkpoint == Checkpoint.INPUT
    assert (rec.action, rec.reason, rec.score) == (Action.BLOCK, "b says block", 1.0)
    assert rec.latency_ms == decision.results[1].latency_ms
    assert rec.decided_by == DecidedBy.RULES
    assert rec.policy_version == "t1"
    assert (rec.tokens, rec.cost) == (0, 0.0)


@pytest.mark.parametrize(
    ("checkpoint", "with_usage"),
    [
        (Checkpoint.INPUT, False),
        (Checkpoint.TOOL_RESULT, False),
        (Checkpoint.TOOL_CALL, True),
        (Checkpoint.OUTPUT, True),
    ],
)
async def test_usage_only_on_first_reply_record(checkpoint, with_usage):
    reply = None
    if checkpoint == Checkpoint.TOOL_CALL:
        reply = Message(role="assistant", tool_calls=[ToolCall(id="1", name="run_shell")])
    elif checkpoint == Checkpoint.OUTPUT:
        reply = Message(role="assistant", content="done")
    sink = MemoryAuditSink()
    sections = {cid: {} for cid in "ab"}
    await _run(
        [FakeCheck("a", 1), FakeCheck("b", 2)],
        sections,
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
    Checkpoint.INPUT: (STEP0, None),
    Checkpoint.TOOL_CALL: (STEP0, Message(role="assistant", tool_calls=CALLS)),
    Checkpoint.TOOL_RESULT: (STEP1, None),
    Checkpoint.OUTPUT: (STEP1, Message(role="assistant", content="3 customers")),
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
        (Checkpoint.INPUT, 0, 2, None, None, 0, False),
        (Checkpoint.TOOL_CALL, 0, 2, 2, "query_customers,run_shell", 2, True),
        (Checkpoint.TOOL_RESULT, 1, 5, None, None, 2, False),
        (Checkpoint.OUTPUT, 1, 5, 0, None, 2, True),
    ],
)
async def test_turn_summary_row_at_each_checkpoint(
    checkpoint, step, messages, tool_calls, tools, total, with_usage
):
    sink = MemoryAuditSink()
    sections = {cid: {} for cid in "ab"}
    decision, _ = await _run(
        [FakeCheck("a", 1), FakeCheck("b", 2)],
        sections,
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
        "action": Action.ALLOW,
        "reason": "allowed",
        "score": 0.0,
        "latency_ms": overhead,
        "decided_by": DecidedBy.RULES,
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
        {},
        request=_session_request(Checkpoint.OUTPUT),
        audit_sink=sink,
        usage=Usage(prompt_tokens=100, completion_tokens=20),
    )
    assert decision.results == [] and decision.action == Action.ALLOW
    [summary] = sink.records
    assert summary.check == "turn_summary"
    assert summary.action == Action.ALLOW
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
    await _run(checks, {"a": {}, "tool_args": {}}, audit_sink=sink)
    summary = sink.records[-1]
    assert (summary.action, summary.reason) == (Action.BLOCK, "blocked by tool_args")
    assert (summary.blocked_by, summary.decided_by, summary.score) == (
        "tool_args",
        DecidedBy.RULES,
        1.0,
    )


@pytest.mark.parametrize("decided_by", ["jev", "fallback"])
async def test_turn_summary_credits_jev_for_its_block(decided_by):
    sink = MemoryAuditSink()
    await _run(
        [JEV],
        {"jev": {}},
        audit_sink=sink,
        judge=FakeJudge(score=0.9, decided_by=decided_by),
    )
    summary = sink.records[-1]
    assert (summary.reason, summary.blocked_by) == ("blocked by jev", "jev")
    assert (summary.decided_by, summary.score) == (DecidedBy(decided_by), 0.9)


async def test_turn_summary_reason_names_redacting_checks():
    sink = MemoryAuditSink()
    checks = [
        FakeCheck("r1", 1, "redact", redactions=[_red(0, 1)]),
        FakeCheck("r2", 2, "redact", redactions=[_red(2, 3)]),
        FakeCheck("a", 3),
    ]
    await _run(checks, {"r1": {}, "r2": {}, "a": {}}, audit_sink=sink)
    redacted = sink.records[-1]
    assert (redacted.action, redacted.reason) == (Action.REDACT, "redacted by r1, r2")


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
    policy = _policy({"a": {}}, price=0.25)

    async def summary(**usage_kw: Any):
        sink = MemoryAuditSink()
        await run_checkpoint(
            _session_request(Checkpoint.OUTPUT),
            policy,
            policy.version,
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


def test_build_context_from_policy():
    policy = _policy({})
    ledger = UsageLedger()
    judge = FakeJudge()
    sigs = [Signature(id="s1", pattern=re.compile("x"))]
    ctx = build_context(policy, ledger=ledger, signatures=sigs, judge=judge)
    assert ctx.ledger is ledger
    assert ctx.signatures is sigs
    assert ctx.jev_threshold == 0.6
    assert ctx.judge is judge
