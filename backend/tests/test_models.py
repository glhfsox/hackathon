from app.models import Checkpoint, Message, ToolCall
from app.models.policy import CheckSection, Mode


def test_tool_call_parses():
    m = Message(role="assistant", tool_calls=[ToolCall(id="1", name="x", arguments={"a": 1})])
    assert m.tool_calls[0].arguments == {"a": 1}


def test_check_config_splits_modes_and_params():
    c = CheckSection.model_validate({"input": "block", "tool_call": "monitor", "max_calls": 5})
    assert c.mode(Checkpoint.INPUT) == Mode.BLOCK
    assert c.mode(Checkpoint.OUTPUT) == Mode.OFF
    assert c.params == {"max_calls": 5}


def _policy(**over):
    from app.models.policy import Policy

    base = {
        "version": "t",
        "active_profile": "balanced",
        "profiles": {
            "balanced": {"jev_threshold": 0.6},
            "strict": {"jev_threshold": 0.4, "checks": {"pii_secrets": {"tool_result": "block"}}},
        },
        "models": {"m": {"upstream_base_url": "http://x/v1"}},
        "checks": {"pii_secrets": {"tool_result": "redact", "types": ["email"]}},
        "jev": {"fallback": {"model": "m", "base_url": "http://x/v1"}},
    }
    base.update(over)
    return Policy.model_validate(base)


def test_profile_overrides_check_mode():
    assert _policy().mode("pii_secrets", Checkpoint.TOOL_RESULT) == Mode.REDACT
    strict = _policy(active_profile="strict")
    assert strict.mode("pii_secrets", Checkpoint.TOOL_RESULT) == Mode.BLOCK
    assert strict.check_config("pii_secrets").params == {"types": ["email"]}


def test_targets_new_vs_all():
    from app.checks.base import targets
    from app.models import CanonicalRequest

    msgs = [
        Message(role="system", content="s"),
        Message(role="user", content="q"),
        Message(role="tool", content="old", tool_call_id="1"),
        Message(role="assistant", content="a"),
        Message(role="assistant", tool_calls=[ToolCall(id="2", name="x")]),
        Message(role="tool", content="new", tool_call_id="2"),
    ]
    req = CanonicalRequest(
        request_id="r",
        caller_id="demo",
        model="m",
        checkpoint=Checkpoint.TOOL_RESULT,
        messages=msgs,
    )
    assert targets(req) == [(5, "new")]
    # Every role: the whole conversation is forwarded upstream, so all of it is inspected.
    everything = [(0, "s"), (1, "q"), (2, "old"), (3, "a"), (5, "new")]
    assert targets(req, scope="all") == everything
    assert targets(req.model_copy(update={"checkpoint": Checkpoint.INPUT}), scope="all") == (
        everything
    )
