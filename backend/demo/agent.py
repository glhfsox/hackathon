"""A small tool-calling agent on the bare `openai` client (no framework).

It is protected by the control layer through two settings only, the client's `base_url` and
`api_key`. The proxy's verdict reaches it as an ordinary chat reply: a refusal ("Blocked by
<check>: <reason>") is taken as the final answer, and no tool call from a blocked reply runs.
Every tool runs through the tool guard (demo.tools.guarded) as well.

The decision trace comes from the response's extra `control` field. The SDK keeps unknown
response fields, so it is `completion.model_extra["control"]` (openai 3.24.0).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import openai

from demo.tools import Tool, guarded

log = logging.getLogger(__name__)

BLOCKED_PREFIX = "Blocked by "


@dataclass(frozen=True)
class AgentEvent:
    """Something the agent saw: a proxy or tool-guard decision, or a tool result."""

    agent: str
    step: int
    kind: Literal["proxy", "guard", "tool"]
    decision: dict[str, Any] | None = None  # proxy and guard: a contract Decision
    tool: str | None = None  # guard and tool
    arguments: dict[str, Any] | None = None  # tool
    result: str | None = None  # tool: the text handed back to the model


@dataclass
class AgentResult:
    answer: str
    blocked: bool  # the answer is a refusal from the control layer
    messages: list[dict[str, Any]]  # the transcript, in OpenAI chat format
    events: list[AgentEvent] = field(default_factory=list)

    @property
    def decisions(self) -> list[dict[str, Any]]:
        """Every control decision seen, proxy and tool guard, in order."""
        return [e.decision for e in self.events if e.decision is not None]


def _dump_call(call: Any) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.function.name, "arguments": call.function.arguments},
    }


def run_agent(
    client: openai.OpenAI,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: Sequence[Tool],
    name: str = "agent",
    max_steps: int = 6,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> AgentResult:
    """Chat until the model answers without a tool call, the control layer blocks, or
    `max_steps` model calls were made."""
    events: list[AgentEvent] = []

    def emit(event: AgentEvent) -> None:
        events.append(event)
        if on_event is not None:
            on_event(event)

    by_name = {t.name: t for t in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    for step in range(1, max_steps + 1):
        completion = client.chat.completions.create(
            model=model, messages=messages, tools=[t.schema for t in tools]
        )
        control = (completion.model_extra or {}).get("control") or {}
        decisions = control.get("decisions") or []
        for decision in decisions:
            emit(AgentEvent(name, step, "proxy", decision=decision))
        message = completion.choices[0].message
        content = message.content or ""
        blocked = content.startswith(BLOCKED_PREFIX) or any(
            d.get("action") == "block" for d in decisions
        )
        if blocked or not message.tool_calls:
            # A blocked reply never carries tool calls to run; the refusal is the answer.
            messages.append({"role": "assistant", "content": content})
            return AgentResult(content, blocked, messages, events)

        history = list(messages)  # the conversation before this reply, as the proxy saw it
        messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [_dump_call(c) for c in message.tool_calls],
            }
        )
        for call in message.tool_calls:
            result = _run_tool(client, by_name, call, history, name, step, emit)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    answer = f"stopped after {max_steps} steps without a final answer"
    return AgentResult(answer, False, messages, events)


def _run_tool(
    client: openai.OpenAI,
    by_name: dict[str, Tool],
    call: Any,
    history: list[dict[str, Any]],
    agent: str,
    step: int,
    emit: Callable[[AgentEvent], None],
) -> str:
    """Run one tool call through the guard. Every failure becomes text for the model."""
    tool_name = call.function.name
    arguments: Any = None
    try:
        tool = by_name.get(tool_name)
        if tool is None:
            raise LookupError(f"unknown tool {tool_name!r}")
        arguments = json.loads(call.function.arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        run = guarded(
            tool.fn,
            client=client,
            name=tool.name,
            on_decision=lambda d: emit(
                AgentEvent(agent, step, "guard", decision=d, tool=tool_name)
            ),
        )
        result = run(call.id, arguments, history)
    except Exception as exc:
        # The model picked the tool and its arguments (unknown tool, wrong names or types): it
        # gets the error back instead of the agent crashing.
        log.warning("%s: tool %s failed: %s", agent, tool_name, exc, exc_info=True)
        result = f"error: {type(exc).__name__}: {exc}"
    shown = arguments if isinstance(arguments, dict) else None
    emit(AgentEvent(agent, step, "tool", tool=tool_name, arguments=shown, result=result))
    return result
