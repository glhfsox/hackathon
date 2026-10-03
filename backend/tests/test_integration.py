"""The real policy file, the production check registry and the real checks, wired together.

Unit tests elsewhere use fake checks or one check at a time; these catch what only shows up when
the pieces meet: the registry the proxy uses, profile switches and what actually reaches Jev.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.checks import get_check, ordered_checks
from app.core.budget import UsageLedger
from app.core.pipeline import run_checkpoint
from app.core.policy_store import parse_policy
from app.core.signatures import load_signatures
from app.models import Action, CanonicalRequest, Checkpoint, Message, Mode, ToolCall, ToolDef
from app.models.policy import CHECK_SPECS, Policy
from app.observability.sinks import MemoryAuditSink
from tests.conftest import FakeJudge

BACKEND_DIR = Path(__file__).resolve().parents[1]
POLICY_FILE = BACKEND_DIR / "policy.yaml"

RAW_SSN = "123-45-6789"
RAW_CARD = "4111 1111 1111 1111"
SENSITIVE = f"my ssn: {RAW_SSN}, card {RAW_CARD}"


def _policy(profile: str) -> Policy:
    raw = yaml.safe_load(POLICY_FILE.read_text())
    raw["active_profile"] = profile
    return parse_policy(yaml.safe_dump(raw))


def _signatures(policy: Policy) -> Any:
    assert policy.signatures is not None
    signatures, _version = load_signatures(policy.signatures.source, base_dir=BACKEND_DIR)
    return signatures


CALL = ToolCall(id="c1", name="query_customers", arguments={"city": "Berlin"})
TOOLS = [ToolDef(name="query_customers", description="look up customers by city")]
USER = Message(role="user", content="How many customers do we have in Berlin?")
STEP1 = [
    USER,
    Message(role="assistant", tool_calls=[CALL]),
    Message(role="tool", content="2 rows: Anna Kowalska, Jan Nowak", tool_call_id="c1"),
]
# One clean agent session: the conversation and reply the pipeline sees at each checkpoint.
SESSION: dict[Checkpoint, tuple[list[Message], Message | None]] = {
    Checkpoint.INPUT: ([USER], None),
    Checkpoint.TOOL_CALL: ([USER], Message(role="assistant", tool_calls=[CALL])),
    Checkpoint.TOOL_RESULT: (STEP1, None),
    Checkpoint.OUTPUT: (STEP1, Message(role="assistant", content="There are 2 customers.")),
}
# Post-round checkpoints of every check (docs/architecture.md §4), cheapest first.
EXPECTED_RUN = {
    Checkpoint.INPUT: [
        "permissions",
        "budget",
        "loop_detection",
        "signatures",
        "pii_secrets",
        "jev",
    ],
    Checkpoint.TOOL_CALL: ["permissions", "signatures", "tool_args"],
    Checkpoint.TOOL_RESULT: [
        "permissions",
        "budget",
        "loop_detection",
        "signatures",
        "pii_secrets",
        "jev",
    ],
    Checkpoint.OUTPUT: ["pii_secrets", "jev"],
}


def _request(checkpoint: Checkpoint, messages: list[Message], reply: Message | None):
    return CanonicalRequest(
        request_id=f"it-{checkpoint.value}",
        caller_id="demo",
        model="gemma4",
        checkpoint=checkpoint,
        messages=messages,
        tools=TOOLS,
        reply=reply,
    )


# --- (a) the production registry ---------------------------------------------------------------


async def test_production_registry_runs_every_check_in_cost_order() -> None:
    policy = _policy("balanced")
    signatures = _signatures(policy)
    ran: set[str] = set()

    for checkpoint, (messages, reply) in SESSION.items():
        judge = FakeJudge(score=0.1)
        decision, _ = await run_checkpoint(
            _request(checkpoint, messages, reply),
            policy,
            policy.version,
            policy.callers["demo"],
            ledger=UsageLedger(),
            signatures=signatures,
            judge=judge,
            audit=MemoryAuditSink(),
        )  # checks=None: the registry the proxy uses

        trace = [(r.check, r.verdict, r.reason) for r in decision.results]
        assert [r.check for r in decision.results] == EXPECTED_RUN[checkpoint], trace
        assert decision.action == Action.ALLOW, trace
        ran.update(r.check for r in decision.results)

    assert ran == {c.id for c in ordered_checks()} and len(ran) == 7
    ranks = [c.cost_rank for c in ordered_checks()]
    assert ranks == sorted(ranks) and len(set(ranks)) == 7


# --- (b) raw PII never reaches Jev, in any profile ---------------------------------------------

PII_REQUESTS = {
    Checkpoint.INPUT: ([Message(role="user", content=SENSITIVE)], None),
    Checkpoint.TOOL_RESULT: (
        [
            Message(role="user", content=f"Check this customer: {SENSITIVE}"),
            Message(role="assistant", tool_calls=[CALL]),
            Message(role="tool", content=f"1 row: Anna, {SENSITIVE}", tool_call_id="c1"),
        ],
        None,
    ),
    Checkpoint.OUTPUT: (
        [Message(role="user", content="Show me Anna's record")],
        Message(role="assistant", content=f"Anna's record: {SENSITIVE}"),
    ),
}


def _all_text(request: CanonicalRequest) -> str:
    texts = [m.content for m in request.messages if m.content]
    if request.reply and request.reply.content:
        texts.append(request.reply.content)
    return "\n".join(texts)


@pytest.mark.parametrize("checkpoint", list(PII_REQUESTS), ids=lambda c: c.value)
@pytest.mark.parametrize("profile", ["balanced", "strict", "permissive"])
async def test_raw_pii_never_reaches_the_judge(profile: str, checkpoint: Checkpoint) -> None:
    policy = _policy(profile)
    messages, reply = PII_REQUESTS[checkpoint]
    request = _request(checkpoint, messages, reply)
    judge = FakeJudge(score=0.1)

    decision, forwarded = await run_checkpoint(
        request,
        policy,
        policy.version,
        policy.callers["demo"],
        judge=judge,
        audit=MemoryAuditSink(),
        checks=[get_check("pii_secrets"), get_check("jev")],
    )

    trace = [(r.check, r.verdict, r.action.value, r.reason) for r in decision.results]
    if policy.mode("pii_secrets", checkpoint) == Mode.BLOCK:
        # strict: PII blocks the request outright, so Jev is never consulted at all.
        assert decision.blocked_by == "pii_secrets", trace
        assert judge.calls == []
        return
    assert [r.check for r in decision.results] == ["pii_secrets", "jev"], trace
    assert judge.calls, "jev did not judge anything, so the test proves nothing"
    for call in judge.calls:
        assert RAW_SSN not in call.text and RAW_CARD not in call.text, call.text
    assert any("[REDACTED:SSN]" in c.text and "[REDACTED:CARD]" in c.text for c in judge.calls)

    # Monitor (permissive) records but does not change what goes upstream; the others redact it.
    if policy.mode("pii_secrets", checkpoint) == Mode.MONITOR:
        assert RAW_SSN in _all_text(forwarded)
    else:
        assert RAW_SSN not in _all_text(forwarded) and RAW_CARD not in _all_text(forwarded)


# --- (c) the policy's static check table matches the registry ----------------------------------


def test_policy_check_table_matches_the_registry() -> None:
    # policy.py cannot import app.checks (import cycle), so it keeps its own table of ids and
    # checkpoints. This keeps the two from drifting apart.
    registry = {c.id: c.checkpoints for c in ordered_checks()}
    table = {cid: spec.checkpoints for cid, spec in CHECK_SPECS.items()}
    assert table == registry


# --- (d) the shipped policy file -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "pii_tool_result", "jev_input"),
    [
        ("strict", Mode.BLOCK, Mode.BLOCK),
        ("balanced", Mode.REDACT, Mode.BLOCK),
        ("permissive", Mode.MONITOR, Mode.MONITOR),
    ],
)
def test_shipped_policy_validates_under_every_profile(
    profile: str, pii_tool_result: Mode, jev_input: Mode
) -> None:
    policy = _policy(profile)

    assert set(policy.profiles) == {"strict", "balanced", "permissive"}
    assert policy.active_profile == profile
    assert policy.mode("pii_secrets", Checkpoint.TOOL_RESULT) == pii_tool_result
    assert policy.mode("jev", Checkpoint.INPUT) == jev_input
    assert set(policy.checks) == set(CHECK_SPECS)


# --- a role pii_secrets skips is never sent to Jev ----------------------------------------------


def test_jev_must_skip_every_role_pii_skips() -> None:
    import yaml

    from app.core.policy_store import PolicyRejectedError, parse_policy

    raw = yaml.safe_load((BACKEND_DIR / "policy.yaml").read_text())
    assert raw["checks"]["jev"]["skip_roles"] == raw["checks"]["pii_secrets"]["skip_roles"]
    raw["checks"]["jev"]["skip_roles"] = []
    with pytest.raises(PolicyRejectedError) as exc:
        parse_policy(yaml.safe_dump(raw))
    assert any(e.loc == "checks.jev.skip_roles" for e in exc.value.errors)


async def test_system_prompt_pii_is_not_sent_to_jev() -> None:
    policy = _policy("balanced")
    judge = FakeJudge(score=0.0)
    request = _request(
        Checkpoint.INPUT,
        [
            Message(role="system", content=f"Support desk. Escalation contact: {SENSITIVE}"),
            Message(role="user", content="What are your opening hours?"),
        ],
        None,
    )
    decision, _ = await run_checkpoint(
        request,
        policy,
        policy.version,
        policy.callers["demo"],
        judge=judge,
        audit=MemoryAuditSink(),
        checks=[get_check("pii_secrets"), get_check("jev")],
    )
    assert decision.action == Action.ALLOW
    assert judge.calls, "the user message must still be judged"
    for call in judge.calls:
        assert RAW_SSN not in call.text and RAW_CARD not in call.text
