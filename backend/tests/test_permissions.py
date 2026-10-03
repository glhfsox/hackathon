import pytest

from app.checks.base import CheckContext
from app.checks.permissions import CHECK
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall

CTX = CheckContext()
SETTINGS = {"allowed_models": ["gemma4"], "allowed_tools": ["query_customers"]}


def _request(
    checkpoint: Checkpoint,
    model: str = "gemma4",
    tools: list[str] | None = None,
    messages: list[Message] | None = None,
):
    reply = None
    if tools is not None:
        calls = [ToolCall(id=str(i), name=name) for i, name in enumerate(tools)]
        reply = Message(role="assistant", tool_calls=calls)
    return CanonicalRequest(
        request_id="r",
        caller_id="anonymous",
        model=model,
        checkpoint=checkpoint,
        messages=messages or [Message(role="user", content="hi")],
        reply=reply,
    )


def _history(tool: str = "query_customers") -> list[Message]:
    """[user, assistant(tool_calls), tool]: what the agent sends after running a tool."""
    return [
        Message(role="user", content="hi"),
        Message(role="assistant", tool_calls=[ToolCall(id="1", name=tool)]),
        Message(role="tool", content="ok", tool_call_id="1"),
    ]


async def test_allowed_model():
    result = await CHECK.run(_request(Checkpoint.INPUT), SETTINGS, CTX)
    assert result.verdict == "allow"
    assert result.check == "permissions"
    assert result.score == 0.0


async def test_disallowed_model_names_model():
    result = await CHECK.run(_request(Checkpoint.INPUT, model="gpt-4o"), SETTINGS, CTX)
    assert result.verdict == "block"
    assert result.score == 1.0
    assert "gpt-4o" in result.reason


async def test_allowed_tool():
    result = await CHECK.run(
        _request(Checkpoint.TOOL_CALL, tools=["query_customers"]), SETTINGS, CTX
    )
    assert result.verdict == "allow"


async def test_disallowed_tool_names_tool():
    req = _request(Checkpoint.TOOL_CALL, tools=["query_customers", "run_shell", "run_shell"])
    result = await CHECK.run(req, SETTINGS, CTX)
    assert result.verdict == "block"
    assert "run_shell" in result.reason
    assert "query_customers" not in result.reason


async def test_tool_call_without_calls_is_an_error():
    result = await CHECK.run(_request(Checkpoint.TOOL_CALL), SETTINGS, CTX)
    assert result.verdict == "error"


def test_metadata():
    assert CHECK.id == "permissions"
    assert CHECK.cost_rank == 1
    assert CHECK.checkpoints == {Checkpoint.INPUT, Checkpoint.TOOL_CALL, Checkpoint.TOOL_RESULT}


async def test_allowed_model_at_tool_result():
    result = await CHECK.run(_request(Checkpoint.TOOL_RESULT, messages=_history()), SETTINGS, CTX)
    assert result.verdict == "allow"
    assert "gemma4" in result.reason


async def test_disallowed_model_at_tool_result_blocks():
    # Red team: a trailing tool message must not skip the model allow-list, because this
    # request is forwarded upstream to request.model just like an input request.
    req = _request(Checkpoint.TOOL_RESULT, model="gpt-4o", messages=_history())
    result = await CHECK.run(req, SETTINGS, CTX)
    assert result.verdict == "block"
    assert "gpt-4o" in result.reason


async def test_tool_calls_in_history_are_not_rejected():
    # Decided: past calls were judged at their own tool_call checkpoint; see the module comment.
    for checkpoint in (Checkpoint.INPUT, Checkpoint.TOOL_RESULT):
        req = _request(checkpoint, messages=_history(tool="run_shell"))
        assert (await CHECK.run(req, SETTINGS, CTX)).verdict == "allow"


async def test_output_checkpoint_is_an_error():
    result = await CHECK.run(_request(Checkpoint.OUTPUT), SETTINGS, CTX)
    assert result.verdict == "error"


# Tool names match exactly: a case or whitespace variant is a different tool and is rejected.
DEV_SETTINGS = {"allowed_models": ["gemma4"], "allowed_tools": ["query_customers", "run_shell"]}


@pytest.mark.parametrize("name", ["Run_Shell", "run_shell ", " run_shell", ""])
async def test_tool_name_variants_are_not_allowed(name):
    result = await CHECK.run(_request(Checkpoint.TOOL_CALL, tools=[name]), DEV_SETTINGS, CTX)
    assert result.verdict == "block"
    # A name that is not a plain identifier is withheld from the reason (it is model-chosen).
    shown = name if name == "Run_Shell" else "<unprintable name>"
    assert repr(shown) in result.reason


async def test_exact_tool_name_is_allowed():
    result = await CHECK.run(_request(Checkpoint.TOOL_CALL, tools=["run_shell"]), DEV_SETTINGS, CTX)
    assert result.verdict == "allow"


EMPTY_SETTINGS = {"allowed_models": [], "allowed_tools": []}


# An allow-list left out of the policy allows nothing, like an empty one.
@pytest.mark.parametrize("settings", [EMPTY_SETTINGS, {}])
@pytest.mark.parametrize("checkpoint", [Checkpoint.INPUT, Checkpoint.TOOL_RESULT])
async def test_empty_allowed_models_blocks_every_model(checkpoint, settings):
    result = await CHECK.run(_request(checkpoint, messages=_history()), settings, CTX)
    assert result.verdict == "block"
    assert "gemma4" in result.reason


@pytest.mark.parametrize("settings", [EMPTY_SETTINGS, {}])
async def test_empty_allowed_tools_blocks_every_tool(settings):
    result = await CHECK.run(
        _request(Checkpoint.TOOL_CALL, tools=["query_customers"]), settings, CTX
    )
    assert result.verdict == "block"
    assert "query_customers" in result.reason
