"""The demo tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.checks import get_check
from app.checks.base import CheckContext
from app.core.pipeline import apply_redactions
from app.models import CanonicalRequest, Checkpoint, Message, ToolCall
from demo import tools
from tests.conftest import FakeUpstream

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


def test_run_shell_runs_in_the_workspace_without_the_secrets(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-jev-key")
    out = tools.run_shell('pwd; ls; echo "key=$TYPESAFE_API_KEY"')

    assert out.startswith("exit code 0")
    assert str(workspace.resolve()) in out and "notes.txt" in out
    assert "test-jev-key" not in out and "key=\n" in out


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
