from unittest.mock import Mock

import pytest

from app.checks.base import CheckContext
from app.checks.budget import CHECK
from app.core.budget import UsageLedger
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall


def _request(checkpoint: Checkpoint = Checkpoint.INPUT) -> CanonicalRequest:
    messages = [Message(role="user", content="hi")]
    if checkpoint == Checkpoint.TOOL_RESULT:
        # [user, assistant(tool_calls), tool]: what the agent sends after running a tool.
        messages += [
            Message(role="assistant", tool_calls=[ToolCall(id="1", name="query_customers")]),
            Message(role="tool", content="ok", tool_call_id="1"),
        ]
    return CanonicalRequest(
        request_id="r",
        caller_id="demo",
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


def _settings(limits: dict[str, float]) -> dict:
    """`limits` as the budget of the role `r`, the only role of the test user."""
    return {"roles": {"r": limits}}


def _ctx(ledger: UsageLedger | None, roles: tuple[str, ...] = ("r",)) -> CheckContext:
    return CheckContext(ledger=ledger, roles=list(roles))


async def _run(ledger: UsageLedger | None, **limits: float):
    """Run the check with `limits` as the user's role budget."""
    return await CHECK.run(_request(), _settings(limits), _ctx(ledger))


def test_metadata():
    assert CHECK.id == "budget"
    assert CHECK.cost_rank == 2
    # tool_result requests are forwarded upstream too, so they must pass the budget.
    assert CHECK.checkpoints == frozenset({Checkpoint.INPUT, Checkpoint.TOOL_RESULT})


async def test_exhausted_budget_blocks_at_tool_result():
    limits = {"requests_per_minute": 30, "tokens_per_day": 50000, "cost_per_day": 0.5}
    ctx = _ctx(_ledger(requests=30, tokens=50000, cost=0.5))
    result = await CHECK.run(_request(Checkpoint.TOOL_RESULT), _settings(limits), ctx)
    assert result.verdict == "block"
    assert result.checkpoint == Checkpoint.TOOL_RESULT
    assert "requests_per_minute exhausted: 30/30" in result.reason
    assert "tokens_per_day exhausted: 50000/50000" in result.reason
    assert "cost_per_day exhausted: 0.5/0.5" in result.reason


async def test_within_budget_allows_at_tool_result():
    ctx = _ctx(_ledger(requests=29))
    settings = _settings({"requests_per_minute": 30})
    result = await CHECK.run(_request(Checkpoint.TOOL_RESULT), settings, ctx)
    assert result.verdict == "allow"


@pytest.mark.parametrize(
    ("used", "verdict"),
    [(2, "allow"), (3, "block"), (4, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_requests_per_minute_boundaries(used, verdict):
    result = await _run(_ledger(requests=used), requests_per_minute=3)
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
    result = await _run(_ledger(tokens=used), tokens_per_day=1000)
    assert result.verdict == verdict
    if verdict == "block":
        assert f"tokens_per_day exhausted: {used}/1000" in result.reason


@pytest.mark.parametrize(
    ("used", "verdict"),
    [(0.99, "allow"), (1.0, "block"), (1.01, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_cost_per_day_boundaries(used, verdict):
    result = await _run(_ledger(cost=used), cost_per_day=1.0)
    assert result.verdict == verdict
    if verdict == "block":
        assert f"cost_per_day exhausted: {used}/1.0" in result.reason


async def test_unlimited_allows_without_touching_ledger():
    ledger = Mock(spec=UsageLedger)
    result = await _run(ledger)
    assert result.verdict == "allow"
    assert result.score == 0.0
    assert ledger.method_calls == []


async def test_unlimited_allows_without_ledger():
    assert (await _run(None)).verdict == "allow"


async def test_missing_ledger_with_a_limit_fails_closed():
    result = await _run(None, tokens_per_day=10)
    assert result.verdict == "error"
    assert "ledger" in result.reason


async def test_unset_limit_is_ignored_even_when_usage_is_high():
    # Only tokens are limited, so a flood of requests and spend must not block.
    ledger = _ledger(requests=500, tokens=10, cost=99.0)
    assert (await _run(ledger, tokens_per_day=1000)).verdict == "allow"


async def test_reason_names_every_exhausted_budget():
    ledger = _ledger(requests=2, tokens=100, cost=1.0)
    result = await _run(ledger, requests_per_minute=2, tokens_per_day=100, cost_per_day=5.0)
    assert result.verdict == "block"
    assert "requests_per_minute exhausted: 2/2" in result.reason
    assert "tokens_per_day exhausted: 100/100" in result.reason
    assert "cost_per_day" not in result.reason


async def test_check_does_not_record_usage():
    ledger = _ledger()
    for _ in range(3):
        await _run(ledger, requests_per_minute=10, tokens_per_day=10, cost_per_day=1.0)
    assert ledger.requests_last_minute("demo") == 0
    assert ledger.tokens_today("demo") == 0


# --- limits per role --------------------------------------------------------------------


ROLES = {
    "support": {"requests_per_minute": 30, "tokens_per_day": 50000},
    "developer": {"requests_per_minute": 60},  # no token limit: unlimited tokens
}


async def test_the_most_generous_limit_of_the_users_roles_applies():
    ledger = _ledger(requests=45, tokens=90000)
    both = await CHECK.run(_request(), {"roles": ROLES}, _ctx(ledger, ("support", "developer")))
    support_only = await CHECK.run(_request(), {"roles": ROLES}, _ctx(ledger, ("support",)))
    assert both.verdict == "allow", both.reason  # 45/60 requests, tokens unlimited
    assert support_only.verdict == "block"
    assert "requests_per_minute exhausted: 45/30" in support_only.reason


@pytest.mark.parametrize("roles", [(), ("guest",)])
async def test_a_user_without_a_role_budget_is_blocked(roles):
    result = await CHECK.run(_request(), {"roles": ROLES}, _ctx(_ledger(), roles))
    assert result.verdict == "block"
    assert "no budget for roles" in result.reason
