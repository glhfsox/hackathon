"""The demo tools and the tool guard wrapper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from app.checks import get_check
from app.checks.base import CheckContext
from app.core.pipeline import apply_redactions
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall
from demo import tools
from tests.demo.conftest import DEMO_KEY, FakeUpstream, bridged_client

PII_FIELDS = ("email", "phone", "ssn", "pesel", "iban", "card")


def test_query_customers_by_name_and_id() -> None:
    by_name = json.loads(tools.query_customers(name="anna"))
    by_id = json.loads(tools.query_customers(customer_id="3"))  # type: ignore[arg-type]

    assert [c["name"] for c in by_name] == ["Anna Kowalska"]
    assert [c["name"] for c in by_id] == ["John Smith"]
    assert len(json.loads(tools.query_customers())) == len(tools.CUSTOMERS)
    assert tools.query_customers(name="nobody") == "no matching customers"


async def test_every_fake_pii_value_is_detected_by_pii_secrets() -> None:
    result_text = tools.query_customers()
    request = CanonicalRequest(
        request_id="r1",
        caller_id="demo",
        model="gemma4",
        checkpoint=Checkpoint.TOOL_RESULT,
        messages=[
            Message(role="user", content="list customers"),
            Message(role="assistant", tool_calls=[ToolCall(id="c1", name="query_customers")]),
            Message(role="tool", content=result_text, tool_call_id="c1"),
        ],
    )

    result = await get_check("pii_secrets").run(request, {}, CheckContext())

    redacted = apply_redactions(request, result.redactions).messages[-1].content or ""
    values = [c[f] for c in tools.CUSTOMERS for f in PII_FIELDS if c[f]]
    assert values and all(v not in redacted for v in values)
    assert all(c["name"] in redacted and c["city"] in redacted for c in tools.CUSTOMERS)


def test_read_file_stays_inside_the_workspace(workspace: Path) -> None:
    outside = workspace.parent / "secret.txt"
    outside.write_text("top secret")
    (workspace / "escape").symlink_to(outside)

    assert tools.read_file("notes.txt").startswith("Release checklist")
    for path in ("../secret.txt", str(outside), "escape", "/etc/hosts"):
        assert tools.read_file(path).startswith("error:"), path
    assert tools.read_file("missing.txt").startswith("error:")


def test_run_shell_runs_in_the_workspace_without_the_agents_secrets(workspace: Path) -> None:
    out = tools.run_shell('pwd; ls; echo "key=$DEMO_API_KEY"')

    assert out.startswith("exit code 0")
    assert str(workspace.resolve()) in out and "notes.txt" in out
    assert "test-demo-key" not in out and "key=\n" in out


def test_run_shell_times_out(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "SHELL_TIMEOUT_S", 0.2)

    assert "timed out" in tools.run_shell("sleep 5")


def test_http_get_only_fetches_allowed_hosts(upstream: FakeUpstream) -> None:
    page = upstream.router.get("http://127.0.0.1:8123/page.html").respond(200, text="hello")
    other = upstream.router.get("http://evil.example/").respond(200, text="never")

    assert tools.http_get("http://127.0.0.1:8123/page.html") == "hello"
    assert tools.http_get("http://evil.example/").startswith("error:")
    assert tools.http_get("file:///etc/passwd").startswith("error:")
    assert page.call_count == 1 and other.call_count == 0


@pytest.fixture
def shell_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Records every subprocess.run call and still runs it."""
    calls: list[Any] = []
    real = subprocess.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools.subprocess, "run", spy)
    return calls


CONVERSATION = [{"role": "user", "content": "Tidy up the workspace."}]


def test_guarded_dangerous_command_is_never_executed(
    gateway: TestClient, workspace: Path, shell_spy: list[Any]
) -> None:
    seen: list[dict[str, Any]] = []
    run = tools.guarded(
        tools.run_shell, client=bridged_client(gateway, DEMO_KEY), on_decision=seen.append
    )

    out = run("c1", {"cmd": "rm -rf ./*"}, CONVERSATION)

    assert out.startswith("Refused by the tool guard: blocked by tool_args: ")
    assert shell_spy == []
    assert (workspace / "notes.txt").exists()
    assert seen[0]["action"] == "block" and seen[0]["checkpoint"] == "tool_call"

    # The same wrapper runs a call the guard allows.
    assert "notes.txt" in run("c2", {"cmd": "ls"}, CONVERSATION)
    assert len(shell_spy) == 1


def test_guard_that_cannot_be_asked_fails_closed(workspace: Path, shell_spy: list[Any]) -> None:
    down = openai.OpenAI(
        base_url="http://gateway.test/v1",
        api_key=DEMO_KEY,
        max_retries=0,
        http_client=httpx2.Client(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(503, json={}))
        ),
    )

    out = tools.guarded(tools.run_shell, client=down)("c1", {"cmd": "ls"}, CONVERSATION)

    assert out.startswith("Refused: the tool guard could not be asked")
    assert shell_spy == []
