"""The Analyst and Operator agents and the handoff between them.

For now both talk to an OpenAI-compatible model endpoint directly (Ollama by default) and run
their tools without asking the tool guard. Putting them behind the control layer later means
pointing `base_url` at the proxy, giving each agent its own key, and adding /v1/tools/check
before every tool call, as demo_data/agent.py does.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import ValidationError

from demo_data.agent import tool_definitions as analyst_tool_definitions
from demo_data.agent import tool_result_content
from test_app import operator_tools

ToolRunner = Callable[[str, dict], dict]

ANALYST_SYSTEM = (
    "You are the Analyst, a read-only treasury assistant for one corporate client. Use "
    "query_transactions for exact payment records and search_documents for supporting "
    "documents. Answer the user's question with the facts you found, cite transaction and "
    "document IDs, and recommend a next action when the user asks what to do. You cannot change "
    "anything yourself; the Operator agent acts on your answer."
)
OPERATOR_SYSTEM = (
    "You are the Operator, a treasury back-office agent. You receive a user request and the "
    "Analyst's findings. Carry out the actions they call for with your tools (send_email, "
    "release_payment, hold_payment, export_report, run_sql) right away, without asking for "
    "confirmation. When no action is needed, say so. Finish with a short summary of what you did."
)


@dataclass
class Step:
    """One tool call the agent made, with the text handed back to the model."""

    step: int
    tool: str
    arguments: dict | str
    result: str


@dataclass
class AgentRun:
    agent: str
    status: Literal["answered", "limit", "error"]
    answer: str
    steps: int
    calls: list[Step] = field(default_factory=list)


@dataclass
class Agent:
    name: str
    system_prompt: str
    tools: list[dict]
    run_tool: ToolRunner


def analyst(run_tool: ToolRunner) -> Agent:
    return Agent("analyst", ANALYST_SYSTEM, analyst_tool_definitions(), run_tool)


def operator(run_tool: ToolRunner) -> Agent:
    return Agent("operator", OPERATOR_SYSTEM, operator_tools.tool_definitions(), run_tool)


def run_agent(
    client: httpx.Client,
    agent: Agent,
    *,
    model: str,
    prompt: str,
    max_steps: int = 6,
    on_step: Callable[[str, Step], None] | None = None,
) -> AgentRun:
    """Chat until the model answers without a tool call or `max_steps` model calls were made."""
    messages = [
        {"role": "system", "content": agent.system_prompt},
        {"role": "user", "content": prompt},
    ]
    calls: list[Step] = []
    for step in range(1, max_steps + 1):
        try:
            response = client.post(
                "chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "tools": agent.tools,
                    "temperature": 0,
                    "stream": False,
                },
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            return AgentRun(agent.name, "error", f"model request failed: {exc!r}", step, calls)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return AgentRun(agent.name, "answered", message.get("content") or "", step, calls)
        messages.append(
            {"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls}
        )
        for call in tool_calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be a JSON object")
                result = tool_result_content(agent.run_tool(name, arguments))
            except (ValueError, ValidationError) as exc:
                # The model chose the tool and arguments; it gets the error back, the run goes on.
                arguments = raw
                result = f"error: {type(exc).__name__}: {exc}"
            record = Step(step, name, arguments, result)
            calls.append(record)
            if on_step is not None:
                on_step(agent.name, record)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    answer = f"stopped after {max_steps} model calls without a final answer"
    return AgentRun(agent.name, "limit", answer, max_steps, calls)


def handoff_prompt(request: str, analyst_answer: str) -> str:
    """Agent A's answer becomes Agent B's input, unfiltered: this is the path the proxy will
    have to inspect."""
    return f"User request:\n{request}\n\nAnalyst findings:\n{analyst_answer}"


def run_pipeline(
    client: httpx.Client,
    *,
    model: str,
    request: str,
    analyst_tools: ToolRunner,
    operator_tools_runner: ToolRunner,
    max_steps: int = 6,
    on_step: Callable[[str, Step], None] | None = None,
) -> list[AgentRun]:
    """Run the Analyst on the request, then the Operator on the Analyst's answer."""
    first = run_agent(
        client,
        analyst(analyst_tools),
        model=model,
        prompt=request,
        max_steps=max_steps,
        on_step=on_step,
    )
    if first.status != "answered":
        return [first]
    second = run_agent(
        client,
        operator(operator_tools_runner),
        model=model,
        prompt=handoff_prompt(request, first.answer),
        max_steps=max_steps,
        on_step=on_step,
    )
    return [first, second]
