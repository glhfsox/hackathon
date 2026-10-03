"""A long agent session through the real pipeline, policy file and Jev client (HTTP mocked).

QA found a benign conversation refused at its 33rd message, and never allowed again, although the
Jev client answers history from its cache. These tests pin that every step pays only for its new
messages, that one request with too much new text fails closed, and that a request's Jev calls run
at most `max_concurrency` at a time. Only the jev check runs, so the other checks' own session
limits do not blur what is measured here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from app.checks.jev import CHECK as JEV_CHECK
from app.core.jev import JevClient
from app.core.pipeline import run_checkpoint
from app.core.policy_store import parse_policy
from app.models import Action, CanonicalRequest, Checkpoint, Message, ToolCall, ToolDef
from app.models.policy import Policy
from app.observability.sinks import MemoryAuditSink

POLICY_FILE = Path(__file__).resolve().parents[1] / "policy.yaml"
TOOLS = [ToolDef(name="query_customers", description="look up customers by city")]
# max_judge_calls is pinned per test so the file's value cannot make a test vacuous: QA met the
# lockout with 32, and 128 is the dev policy's cap.
QA_CAP, CAP = 32, 128


def _policy(max_judge_calls: int = CAP) -> Policy:
    raw = yaml.safe_load(POLICY_FILE.read_text())
    raw["checks"]["jev"]["max_judge_calls"] = max_judge_calls
    return parse_policy(yaml.safe_dump(raw))


class Jev:
    """The mocked Jev endpoint: a benign answer after a short real wait, so calls overlap."""

    def __init__(self) -> None:
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        answers = {
            "risky": {"type": "noul", "noul": 0.05},
            "category": {"type": "choice", "choice": "benign"},
        }
        return httpx.Response(200, json={"answers": answers})


@pytest.fixture
def policy(monkeypatch) -> Policy:
    policy = _policy()
    monkeypatch.setenv(policy.jev.api_key_env, "test-jev-key")
    return policy


@pytest.fixture
async def setup(policy):
    jev = Jev()
    async with httpx.AsyncClient() as http:
        # Unmatched requests raise inside respx, so nothing reaches a real service.
        with respx.mock(assert_all_called=False) as router:
            router.post(f"{policy.jev.base_url.rstrip('/')}/v1/systemone").mock(side_effect=jev)
            fallback = router.post(f"{policy.jev.fallback.base_url.rstrip('/')}/chat/completions")
            yield jev, fallback, JevClient(lambda: policy.jev, http)


async def _step(policy, client, checkpoint, messages, reply=None):
    request = CanonicalRequest(
        request_id=f"session-{len(messages)}-{checkpoint.value}",
        caller_id="demo",
        model="gemma4",
        checkpoint=checkpoint,
        messages=list(messages),
        tools=TOOLS,
        reply=reply,
    )
    decision, _ = await run_checkpoint(
        request,
        policy,
        policy.version,
        judge=client,
        audit=MemoryAuditSink(),
        checks=[JEV_CHECK],
    )
    return decision


async def test_long_benign_session_is_allowed_and_pays_only_for_new_messages(setup):
    jev, fallback, client = setup
    policy = _policy(QA_CAP)
    messages = [Message(role="system", content="You are the support agent of ACME.")]
    steps = 0

    async def step(checkpoint, reply=None, *, new: int) -> None:
        nonlocal steps
        before = jev.calls
        decision = await _step(policy, client, checkpoint, messages, reply)
        trace = [(r.check, r.verdict, r.reason) for r in decision.results]
        assert decision.action == Action.ALLOW, (len(messages), trace)
        assert jev.calls - before == new, (len(messages), checkpoint)
        steps += 1

    turn = 0
    while len(messages) < 60:
        messages.append(Message(role="user", content=f"Turn {turn}: customers in city {turn}?"))
        await step(Checkpoint.INPUT, new=1)  # the dev policy does not send system to Jev
        if turn % 2 == 0:
            call = ToolCall(id=f"c{turn}", name="query_customers", arguments={"city": turn})
            messages.append(Message(role="assistant", tool_calls=[call]))
            messages.append(
                Message(role="tool", content=f"{turn} rows for city {turn}", tool_call_id=call.id)
            )
            await step(Checkpoint.TOOL_RESULT, new=1)
        reply = Message(role="assistant", content=f"City {turn} has {turn} customers.")
        await step(Checkpoint.OUTPUT, reply, new=1)
        messages.append(reply)
        turn += 1

    with_content = [m for m in messages if m.content]
    assert len(messages) >= 60 and len(with_content) > QA_CAP + 8  # well past the old lockout
    # Every judged message was judged exactly once over the whole session, never by the
    # fallback. The system prompt is not sent to Jev (jev.skip_roles in the dev policy).
    judged = [m for m in with_content if m.role != "system"]
    assert jev.calls == len(judged) and not fallback.called
    assert steps > 40


async def test_identical_tool_results_cost_one_call(policy, setup):
    jev, _, client = setup
    call = ToolCall(id="c1", name="query_customers", arguments={"city": "Krakow"})
    messages = [
        Message(role="user", content="Check the two backups."),
        Message(role="assistant", tool_calls=[call]),
        Message(role="tool", content="ok", tool_call_id="c1"),
        Message(role="tool", content="ok", tool_call_id="c1"),
    ]

    decision = await _step(policy, client, Checkpoint.TOOL_RESULT, messages)

    assert decision.action == Action.ALLOW and jev.calls == 2


async def test_one_request_with_too_much_new_text_fails_closed(policy, setup):
    jev, fallback, client = setup
    messages = [Message(role="user", content=f"note {i}: nothing special") for i in range(200)]

    decision = await _step(policy, client, Checkpoint.INPUT, messages)

    assert decision.action == Action.BLOCK and decision.blocked_by == "jev"
    (result,) = decision.results
    assert result.verdict == "error"
    assert result.reason == (
        f"conversation too large to judge: 200 new chunks exceed max_judge_calls {CAP}"
    )
    assert jev.calls == 0 and not fallback.called


async def test_jev_calls_never_exceed_max_concurrency(policy, setup):
    jev, _, client = setup
    limit = policy.check_config("jev").params.get("max_concurrency", 8)
    messages = [Message(role="user", content=f"note {i}") for i in range(3 * limit + 1)]

    decision = await _step(policy, client, Checkpoint.INPUT, messages)

    assert decision.action == Action.ALLOW
    assert jev.calls == len(messages) and jev.max_in_flight == limit
