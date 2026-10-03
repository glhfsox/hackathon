from app.models import Checkpoint, Message, ToolCall
from app.models.policy import CheckSection


def test_tool_call_parses():
    m = Message(role="assistant", tool_calls=[ToolCall(id="1", name="x", arguments={"a": 1})])
    assert m.tool_calls[0].arguments == {"a": 1}


def test_check_section_is_params_only():
    assert CheckSection.model_validate({"max_calls": 5}).params == {"max_calls": 5}
    # A bare `tool_args:` in YAML is None: the check is on with its defaults.
    assert CheckSection.model_validate(None).params == {}


def _policy(**over):
    from app.models.policy import Policy

    base = {
        "version": "t",
        "jev_threshold": 0.6,
        "models": {"m": {"upstream_base_url": "http://x/v1"}},
        "checks": {"pii_secrets": {"types": ["email"]}},
        "jev": {"fallback": {"model": "m", "base_url": "http://x/v1"}},
    }
    base.update(over)
    return Policy.model_validate(base)


def test_a_check_is_on_when_its_section_exists():
    policy = _policy()
    assert policy.enabled("pii_secrets") and not policy.enabled("tool_args")
    assert policy.check_config("pii_secrets").params == {"types": ["email"]}
    assert policy.check_config("tool_args").params == {}


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
