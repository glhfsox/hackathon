from typing import Any

import pytest

from app.checks.base import CheckContext
from app.checks.loop_detection import CHECK
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall


def _request(
    calls: list[tuple[str, dict[str, Any]]], checkpoint: Checkpoint = Checkpoint.tool_result
) -> CanonicalRequest:
    """A conversation where each call is its own assistant step followed by its tool result."""
    messages = [Message(role="user", content="do the task")]
    for i, (name, args) in enumerate(calls):
        call_id = str(i)
        messages.append(
            Message(role="assistant", tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])
        )
        messages.append(Message(role="tool", content="ok", tool_call_id=call_id))
    if checkpoint == Checkpoint.input:
        messages.append(Message(role="user", content="continue"))
    return CanonicalRequest(
        request_id="r", caller_id="demo", model="gemma4", checkpoint=checkpoint, messages=messages
    )


def _distinct(n: int) -> list[tuple[str, dict[str, Any]]]:
    return [("run_shell", {"cmd": f"echo {i}"}) for i in range(n)]


async def _run(request: CanonicalRequest, **settings: Any):
    return await CHECK.run(request, settings, CheckContext())


def test_metadata():
    assert CHECK.id == "loop_detection"
    assert CHECK.cost_rank == 3
    assert CHECK.checkpoints == frozenset({Checkpoint.input, Checkpoint.tool_result})


@pytest.mark.parametrize(
    ("n", "verdict"),
    [(2, "allow"), (3, "allow"), (4, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_max_tool_calls_boundaries(n, verdict):
    result = await _run(_request(_distinct(n)), max_tool_calls=3)
    assert result.verdict == verdict
    if verdict == "block":
        assert "max_tool_calls" in result.reason
        assert "4 tool calls" in result.reason
        assert "limit 3" in result.reason


@pytest.mark.parametrize(
    ("n", "verdict"),
    [(1, "allow"), (2, "allow"), (3, "block")],
    ids=["one-under", "at-limit", "one-over"],
)
async def test_max_repeats_boundaries(n, verdict):
    calls = [("run_shell", {"cmd": "ls"})] * n
    result = await _run(_request(calls), max_repeats=2)
    assert result.verdict == verdict
    if verdict == "block":
        assert "max_repeats" in result.reason
        assert "run_shell" in result.reason
        assert "3 times" in result.reason
        assert "limit 2" in result.reason


async def test_argument_order_does_not_matter():
    calls = [
        ("query_customers", {"id": 1, "filter": {"a": 1, "b": 2}}),
        ("query_customers", {"filter": {"b": 2, "a": 1}, "id": 1}),
    ]
    result = await _run(_request(calls), max_repeats=1)
    assert result.verdict == "block"
    assert "query_customers" in result.reason


async def test_different_arguments_are_not_repeats():
    calls = [("query_customers", {"id": i}) for i in range(5)]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"


async def test_same_arguments_on_different_tools_are_not_repeats():
    calls = [("run_shell", {"x": 1}), ("query_customers", {"x": 1})]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"


async def test_parallel_tool_calls_in_one_message_are_counted():
    calls = [ToolCall(id=str(i), name="run_shell", arguments={"cmd": "ls"}) for i in range(3)]
    request = CanonicalRequest(
        request_id="r",
        caller_id="demo",
        model="gemma4",
        checkpoint=Checkpoint.tool_result,
        messages=[
            Message(role="user", content="go"),
            Message(role="assistant", tool_calls=calls),
            *[Message(role="tool", content="ok", tool_call_id=str(i)) for i in range(3)],
        ],
    )
    assert (await _run(request, max_tool_calls=2)).verdict == "block"
    assert (await _run(request, max_repeats=2)).verdict == "block"
    assert (await _run(request, max_tool_calls=3, max_repeats=3)).verdict == "allow"


# QA: limits counted over the whole conversation capped a support chat at ~10 tool-backed
# questions per session. They count one agent turn: the tool calls since the last user message.


def _turns(*turns: list[tuple[str, dict[str, Any]]], checkpoint=Checkpoint.tool_result):
    """A conversation of user turns; every turn but the last ends with the assistant's answer."""
    messages: list[Message] = []
    n = 0
    for t, calls in enumerate(turns):
        messages.append(Message(role="user", content=f"question {t}"))
        for name, args in calls:
            call_id = str(n := n + 1)
            messages.append(
                Message(
                    role="assistant", tool_calls=[ToolCall(id=call_id, name=name, arguments=args)]
                )
            )
            messages.append(Message(role="tool", content="ok", tool_call_id=call_id))
        if t < len(turns) - 1:
            messages.append(Message(role="assistant", content=f"answer {t}"))
    if checkpoint == Checkpoint.input:
        messages += [Message(role="assistant", content="done"), Message(role="user", content="?")]
    return CanonicalRequest(
        request_id="r", caller_id="demo", model="gemma4", checkpoint=checkpoint, messages=messages
    )


async def test_earlier_turns_do_not_count():
    ls = ("run_shell", {"cmd": "ls"})
    request = _turns([ls] * 3 + _distinct(4), [ls])
    result = await _run(request, max_tool_calls=3, max_repeats=2)
    assert result.verdict == "allow", result.reason
    assert result.reason == "1 tool calls since the last user message, at most 1 identical"


async def test_long_session_of_small_turns_is_allowed_at_every_step():
    ask = [("query_customers", {"id": 42}), ("query_customers", {"id": 43})]
    for n in range(1, 21):
        result = await _run(_turns(*[ask] * n), max_tool_calls=10, max_repeats=3)
        assert result.verdict == "allow", (n, result.reason)


async def test_runaway_loop_inside_one_turn_still_blocks():
    ls = ("run_shell", {"cmd": "ls"})
    request = _turns(_distinct(2), [ls] * 4)
    result = await _run(request, max_repeats=3)
    assert result.verdict == "block"
    assert result.reason == (
        "max_repeats exceeded: run_shell called 4 times with identical arguments "
        "since the last user message (limit 3)"
    )
    result = await _run(_turns([ls], _distinct(11)), max_tool_calls=10)
    assert result.verdict == "block"
    assert result.reason == (
        "max_tool_calls exceeded: 11 tool calls since the last user message (limit 10)"
    )


async def test_input_checkpoint_starts_a_new_turn():
    # A new user message opens a new turn, so the history of the previous one does not count.
    result = await _run(_request(_distinct(4), Checkpoint.input), max_tool_calls=3, max_repeats=0)
    assert result.verdict == "allow", result.reason


async def test_without_a_user_message_everything_counts():
    request = _request(_distinct(4))
    request.messages = request.messages[1:]
    assert (await _run(request, max_tool_calls=3)).verdict == "block"


async def test_both_settings_missing_allows():
    calls = [("run_shell", {"cmd": "ls"})] * 50
    result = await _run(_request(calls))
    assert result.verdict == "allow"
    assert result.score == 0.0


async def test_missing_max_repeats_turns_that_rule_off():
    calls = [("run_shell", {"cmd": "ls"})] * 5
    assert (await _run(_request(calls), max_tool_calls=10)).verdict == "allow"


async def test_missing_max_tool_calls_turns_that_rule_off():
    assert (await _run(_request(_distinct(50)), max_repeats=1)).verdict == "allow"


async def test_none_setting_is_treated_as_missing():
    calls = [("run_shell", {"cmd": "ls"})] * 5
    assert (await _run(_request(calls), max_tool_calls=None, max_repeats=None)).verdict == "allow"


async def test_no_tool_calls_allows():
    request = CanonicalRequest(
        request_id="r",
        caller_id="demo",
        model="gemma4",
        checkpoint=Checkpoint.input,
        messages=[Message(role="user", content="hello")],
    )
    assert (await _run(request, max_tool_calls=0, max_repeats=0)).verdict == "allow"


@pytest.mark.parametrize("bad", ["10", -1, 2.5, True])
async def test_invalid_setting_is_an_error(bad):
    result = await _run(_request(_distinct(1)), max_tool_calls=bad)
    assert result.verdict == "error"
    assert "max_tool_calls" in result.reason


# Red team: whitespace padding must not make a repeated call look new.
@pytest.mark.parametrize(
    "variants",
    [
        ["ls", "ls ", " ls", "ls\t"],
        ["ls -la", "ls  -la", "ls\n-la", " ls \t -la "],
        ["ls", "ls ", "ls\r\n", " ls"],
    ],
    ids=["edges", "inner", "unicode"],
)
async def test_whitespace_variants_are_repeats(variants):
    calls = [("run_shell", {"cmd": v}) for v in variants]
    result = await _run(_request(calls), max_repeats=3)
    assert result.verdict == "block"
    assert "run_shell called 4 times" in result.reason


async def test_whitespace_is_normalized_in_nested_values():
    calls = [
        ("query_customers", {"filter": {"name": "bob"}, "fields": ["id", "name"]}),
        ("query_customers", {"filter": {"name": " bob "}, "fields": ["id ", "name"]}),
        ("query_customers", {"fields": ["id", "  name"], "filter": {"name": "bob\n"}}),
    ]
    result = await _run(_request(calls), max_repeats=2)
    assert result.verdict == "block"
    assert "query_customers called 3 times" in result.reason


async def test_case_is_not_folded():
    # Shell commands and SQL identifiers can be case sensitive, so these are different calls.
    calls = [("run_shell", {"cmd": "ls"}), ("run_shell", {"cmd": "LS"})]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"


async def test_whitespace_inside_a_value_still_separates_words():
    calls = [("run_shell", {"cmd": "rm -rf a b"}), ("run_shell", {"cmd": "rm -rf ab"})]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"


async def test_non_string_values_are_not_coerced():
    calls = [("query_customers", {"id": 1}), ("query_customers", {"id": "1"})]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"


async def test_varied_counter_is_caught_only_by_max_tool_calls():
    # A deliberately varied argument defeats max_repeats by design; max_tool_calls bounds it.
    calls = [("run_shell", {"cmd": "ls", "n": i}) for i in range(5)]
    assert (await _run(_request(calls), max_repeats=1)).verdict == "allow"
    result = await _run(_request(calls), max_repeats=1, max_tool_calls=4)
    assert result.verdict == "block"
    assert "max_tool_calls exceeded" in result.reason
