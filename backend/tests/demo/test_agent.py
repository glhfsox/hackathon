"""The agent loop and the two agents, against the control layer with a scripted upstream."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.observability.sinks import read_jsonl
from demo import tools
from demo.agent import AgentEvent
from demo.agents import run_orchestrator, run_worker
from demo.run import INJECTION_PAGE
from tests.conftest import FakeUpstream, completion
from tests.demo.conftest import bridged_client


def _trace(events: list[AgentEvent]) -> list[tuple[str, str, str]]:
    """(kind, checkpoint or tool, action) per event."""
    out = []
    for e in events:
        if e.decision is not None:
            out.append((e.kind, e.decision["checkpoint"], e.decision["action"]))
        else:
            out.append((e.kind, e.tool or "", "ran"))
    return out


def test_benign_two_step_tool_flow(
    gateway: TestClient, upstream: FakeUpstream, workspace: Path
) -> None:
    upstream.script(
        completion(tool_calls=[("read_file", {"path": "notes.txt"})]),
        completion("It is the release checklist for v2.3."),
    )

    result = run_worker(bridged_client(gateway), "Summarize notes.txt.")

    assert result.answer == "It is the release checklist for v2.3." and not result.blocked
    assert _trace(result.events) == [
        ("proxy", "input", "allow"),
        ("proxy", "tool_call", "allow"),
        ("tool", "read_file", "ran"),
        ("proxy", "tool_result", "allow"),
        ("proxy", "output", "allow"),
    ]
    tool_message = upstream.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["content"].startswith("Release")
    assert [m["role"] for m in result.messages] == ["system", "user", "assistant", "tool"] + [
        "assistant"
    ]


@pytest.fixture
def shell_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    real = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools.subprocess, "run", spy)
    return calls


def test_a_refusal_is_the_answer_and_its_tool_calls_never_run(
    gateway: TestClient, upstream: FakeUpstream, workspace: Path, shell_spy: list[Any]
) -> None:
    upstream.script(completion(tool_calls=[("run_shell", {"cmd": "rm -rf ./*"})]))

    result = run_worker(bridged_client(gateway), "Clean up the workspace.")

    assert result.blocked and result.answer.startswith("Blocked by tool_args: ")
    assert _trace(result.events) == [("proxy", "input", "allow"), ("proxy", "tool_call", "block")]
    assert shell_spy == [] and (workspace / "notes.txt").exists()
    assert len(upstream.requests) == 1


def test_hidden_instructions_in_a_fetched_page_never_reach_the_model(
    gateway: TestClient, upstream: FakeUpstream, workspace: Path
) -> None:
    url = "http://127.0.0.1:8123/pricing.html"
    upstream.router.get(url).respond(200, text=INJECTION_PAGE)
    upstream.script(completion(tool_calls=[("http_get", {"url": url})]))

    result = run_worker(bridged_client(gateway), f"What does {url} say?")

    assert result.blocked and result.answer.startswith("Blocked by signatures: ")
    assert _trace(result.events)[-1] == ("proxy", "tool_result", "block")
    assert len(upstream.requests) == 1


def test_delegate_runs_the_worker_inside_the_orchestrators_tool_call(
    gateway: TestClient, upstream: FakeUpstream, workspace: Path, logs_dir: Path
) -> None:
    task = "Look up the city of customer Jan Nowak."
    upstream.script(
        completion(tool_calls=[("delegate", {"task": task})]),  # orchestrator
        completion(tool_calls=[("query_customers", {"name": "Jan Nowak"})]),  # worker
        completion("Jan Nowak lives in Warszawa."),  # worker
        completion("He lives in Warszawa."),  # orchestrator
    )
    events: list[AgentEvent] = []

    result = run_orchestrator(
        bridged_client(gateway, ("orchestrator",)),
        bridged_client(gateway),
        "Which city does Jan Nowak live in?",
        on_event=events.append,
    )

    assert result.answer == "He lives in Warszawa." and not result.blocked
    # The worker ran inside the orchestrator's delegate call.
    assert [(e.agent, e.tool) for e in events if e.kind == "tool"] == [
        ("worker", "query_customers"),
        ("orchestrator", "delegate"),
    ]
    assert ("orchestrator", "tool_call", "allow") in [
        (e.agent, e.decision["checkpoint"], e.decision["action"]) for e in events if e.decision
    ]
    worker_first, worker_second, orchestrator_second = upstream.requests[1:]
    assert worker_first["messages"][1] == {"role": "user", "content": task}
    # The worker's model only saw the customer record with its PII redacted.
    record = worker_second["messages"][-1]["content"]
    assert "Warszawa" in record and "92031544213" not in record and "[REDACTED:PESEL]" in record
    # The worker's answer came back to the orchestrator as a tool result.
    assert orchestrator_second["messages"][-1]["content"] == "Jan Nowak lives in Warszawa."
    callers = {r.caller_id for r in read_jsonl(logs_dir) if r.check == "turn_summary"}
    assert callers == {"orchestrator", "developer"}
