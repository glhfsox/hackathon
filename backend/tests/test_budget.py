from unittest.mock import Mock

import pytest

from app.checks.base import CheckContext
from app.checks.budget import CHECK
from app.core.budget import UsageLedger
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall


def _request(
    caller_id: str = "demo", checkpoint: Checkpoint = Checkpoint.INPUT
) -> CanonicalRequest:
    messages = [Message(role="user", content="hi")]
    if checkpoint == Checkpoint.TOOL_RESULT:
        # [user, assistant(tool_calls), tool]: what the agent sends after running a tool.
        messages += [
            Message(role="assistant", tool_calls=[ToolCall(id="1", name="query_customers")]),
            Message(role="tool", content="ok", tool_call_id="1"),
        ]
    return CanonicalRequest(
        request_id="r",
        caller_id=caller_id,
        model="gemma4",
        checkpoint=checkpoint,
        messages=messages,
    )


def _ledger(requests: int = 0, tokens: int = 0, cost: float = 0.0, caller: str = "demo"):
    # A frozen clock keeps every preloaded request inside the minute window.
    ledger = UsageLedger(clock=lambda: 1_000_000.0)
    for _ in range(requests):
        ledger.record_request(caller)
    if tokens or cost:
        ledger.record_usage(caller, tokens, cost)
    return ledger


async def _run(ctx: CheckContext, caller_id: str = "demo"):
    return await CHECK.run(_request(caller_id), {}, ctx)


def test_metadata():
    assert CHECK.id == "budget"
    assert CHECK.cost_rank == 2
    # tool_result requests are forwarded upstream too, so they must pass the budget.
    assert CHECK.checkpoints == frozenset({Checkpoint.INPUT, Checkpoint.TOOL_RESULT})


async def test_exhausted_budget_blocks_at_tool_result():
    ctx = CheckContext(
        requests_per_minute=30,
        tokens_per_day=50000,
        cost_per_day=0.5,
        ledger=_ledger(requests=30, tokens=50000, cost=0.5, caller="support"),
    )
    result = await CHECK.run(_request("support", Checkpoint.TOOL_RESULT), {}, ctx)
    assert result.verdict == "block"
    assert result.checkpoint == Checkpoint.TOOL_RESULT
    assert "requests_per_minute exhausted: 30/30" in result.reason
    assert "tokens_per_day exhausted: 50000/50000" in result.reason
    assert "cost_per_day exhausted: 0.5/0.5" in result.reason


async def test_within_budget_allows_at_tool_result():
    ctx = CheckContext(requests_per_minute=30, ledger=_ledger(requests=29, caller="support"))
    result = await CHECK.run(_request("support", Checkpoint.TOOL_RESULT), {}, ctx)
    assert result.verdict == "allow"


@pytest.mark.parametrize(
    ("used", "verdict"),
    [(2, "allow"), (3, "block"), (4, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_requests_per_minute_boundaries(used, verdict):
    ctx = CheckContext(requests_per_minute=3, ledger=_ledger(requests=used))
    result = await _run(ctx)
    assert result.verdict == verdict
    if verdict == "block":
        assert f"requests_per_minute exhausted: {used}/3" in result.reason
        assert result.score == 1.0


@pytest.mark.parametrize(
    ("used", "verdict"),
    [(999, "allow"), (1000, "block"), (1001, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_tokens_per_day_boundaries(used, verdict):
    ctx = CheckContext(tokens_per_day=1000, ledger=_ledger(tokens=used))
    result = await _run(ctx)
    assert result.verdict == verdict
    if verdict == "block":
        assert f"tokens_per_day exhausted: {used}/1000" in result.reason


@pytest.mark.parametrize(
    ("used", "verdict"),
    [(0.99, "allow"), (1.0, "block"), (1.01, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_cost_per_day_boundaries(used, verdict):
    ctx = CheckContext(cost_per_day=1.0, ledger=_ledger(cost=used))
    result = await _run(ctx)
    assert result.verdict == verdict
    if verdict == "block":
        assert f"cost_per_day exhausted: {used}/1.0" in result.reason


async def test_unlimited_allows_without_touching_ledger():
    ledger = Mock(spec=UsageLedger)
    result = await _run(CheckContext(ledger=ledger))
    assert result.verdict == "allow"
    assert result.score == 0.0
    assert ledger.method_calls == []


async def test_unlimited_allows_without_ledger():
    assert (await _run(CheckContext(ledger=None))).verdict == "allow"


async def test_missing_ledger_with_a_limit_fails_closed():
    result = await _run(CheckContext(tokens_per_day=10, ledger=None))
    assert result.verdict == "error"
    assert "ledger" in result.reason


async def test_unset_limit_is_ignored_even_when_usage_is_high():
    # Only tokens are limited, so a flood of requests and spend must not block.
    ctx = CheckContext(tokens_per_day=1000, ledger=_ledger(requests=500, tokens=10, cost=99.0))
    assert (await _run(ctx)).verdict == "allow"


async def test_reason_names_every_exhausted_budget():
    ctx = CheckContext(
        requests_per_minute=2,
        tokens_per_day=100,
        cost_per_day=5.0,
        ledger=_ledger(requests=2, tokens=100, cost=1.0),
    )
    result = await _run(ctx)
    assert result.verdict == "block"
    assert "requests_per_minute exhausted: 2/2" in result.reason
    assert "tokens_per_day exhausted: 100/100" in result.reason
    assert "cost_per_day" not in result.reason


async def test_usage_is_read_for_the_request_caller_only():
    ctx = CheckContext(requests_per_minute=1, ledger=_ledger(requests=5, caller="other"))
    assert (await _run(ctx, caller_id="demo")).verdict == "allow"
    assert (await _run(ctx, caller_id="other")).verdict == "block"


async def test_check_does_not_record_usage():
    ledger = _ledger()
    ctx = CheckContext(requests_per_minute=10, tokens_per_day=10, cost_per_day=1.0, ledger=ledger)
    for _ in range(3):
        await _run(ctx)
    assert ledger.requests_last_minute("demo") == 0
    assert ledger.tokens_today("demo") == 0
