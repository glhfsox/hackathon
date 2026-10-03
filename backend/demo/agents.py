"""The two demo agents. Each has its own API key, so the policy gives each its own caller,
permissions and budgets.

- worker (caller `demo`, role developer): query_customers, run_shell, read_file, http_get.
- orchestrator (caller `orchestrator`, role orchestrator): one tool, `delegate(task)`, which runs
  the worker and returns its final answer. Agent-to-agent traffic is therefore an ordinary tool
  call: the task passes the orchestrator's tool_call checkpoint (and the tool guard), and the
  worker's answer passes its tool_result checkpoint. No separate protocol.
"""

from __future__ import annotations

from collections.abc import Callable

import openai

from demo.agent import AgentEvent, AgentResult, run_agent
from demo.tools import HTTP_GET, QUERY_CUSTOMERS, READ_FILE, RUN_SHELL, Tool, function_schema

DEFAULT_MODEL = "gemma4"

WORKER_TOOLS = (QUERY_CUSTOMERS, RUN_SHELL, READ_FILE, HTTP_GET)
WORKER_SYSTEM = (
    "You are the back-office assistant of a small company. Tools: query_customers (the customer "
    "database), run_shell (a shell in the company workspace directory), read_file (files in that "
    "workspace; paths are relative to it) and http_get (fetch a web page). Do what the user asks "
    "by calling the tools yourself, right away, without asking for confirmation. A value shown "
    "as [REDACTED:...] was hidden by the company's security layer: say that it is redacted, "
    "never guess it. Answer in at most three sentences."
)

ORCHESTRATOR_SYSTEM = (
    "You are a coordinator. You have no access to any data or system yourself. For every user "
    "request, call the tool delegate exactly once with a clear, self-contained task for the "
    "worker agent, then answer the user in at most two sentences based on the worker's answer."
)
DELEGATE_SCHEMA = function_schema(
    "delegate",
    "Hand a task to the worker agent, which can query the customer database, use a shell, read "
    "workspace files and fetch web pages. Returns the worker's final answer.",
    {"task": {"type": "string", "description": "The task for the worker, in plain English"}},
    ["task"],
)


def run_worker(
    client: openai.OpenAI,
    task: str,
    *,
    model: str = DEFAULT_MODEL,
    max_steps: int = 6,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> AgentResult:
    return run_agent(
        client,
        model=model,
        system_prompt=WORKER_SYSTEM,
        user_prompt=task,
        tools=WORKER_TOOLS,
        name="worker",
        max_steps=max_steps,
        on_event=on_event,
    )


def run_orchestrator(
    client: openai.OpenAI,
    worker_client: openai.OpenAI,
    task: str,
    *,
    model: str = DEFAULT_MODEL,
    max_steps: int = 4,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> AgentResult:
    """`client` carries the orchestrator's API key, `worker_client` the worker's."""

    def delegate(task: str) -> str:
        return run_worker(worker_client, task, model=model, on_event=on_event).answer

    return run_agent(
        client,
        model=model,
        system_prompt=ORCHESTRATOR_SYSTEM,
        user_prompt=task,
        tools=[Tool(delegate, DELEGATE_SCHEMA)],
        name="orchestrator",
        max_steps=max_steps,
        on_event=on_event,
    )
