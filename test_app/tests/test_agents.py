"""Offline tests: a small generated corpus and a scripted model endpoint, no Ollama needed."""

import json
from pathlib import Path

import httpx
import pytest

from demo_data.generate import generate
from demo_data.models import GenerationConfig
from demo_data.storage import export
from test_app.__main__ import main
from test_app.agents import analyst, handoff_prompt, operator, run_agent, run_pipeline
from test_app.analyst_tools import CorpusTools
from test_app.operator_tools import OperatorTools

CONFIG = (
    '{"schema_version": 1, "seed": 7, "clients": 4, "transactions": 40, '
    '"reference_date": "2026-10-03"}'
)


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    config = GenerationConfig.model_validate_json(CONFIG)
    output = tmp_path_factory.mktemp("corpus") / "treasury"
    export(generate(config), config, output)
    return output


def call(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def scripted(*messages: dict) -> tuple[httpx.Client, list[dict]]:
    """A model that returns `messages` in order and records every request body."""
    queue, requests = list(messages), []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": queue.pop(0)}]})

    client = httpx.Client(base_url="http://model.test/v1/", transport=httpx.MockTransport(handler))
    return client, requests


def tool_reply(*calls: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


def test_search_is_scoped_to_the_bound_client(corpus_dir: Path) -> None:
    tools = CorpusTools(corpus_dir, "CLI-0001")
    hits = tools.run("search_documents", {"query": "supplier invoice clarification", "k": 10})
    assert hits["chunks"]
    for hit in hits["chunks"]:
        assert hit["client_id"] in ("CLI-0001", None)
        doc = next(d for d in tools.corpus.documents if d.document_id == hit["document_id"])
        assert doc.text[hit["char_start"] : hit["char_end"]] == hit["text"]


def test_poisoned_note_is_retrievable(corpus_dir: Path) -> None:
    tools = CorpusTools(corpus_dir, "CLI-0001")
    hits = tools.run(
        "search_documents",
        {"query": "processing note instructions", "k": 10, "transaction_id": "TXN-000001"},
    )
    assert any("Processing note" in hit["section_path"] for hit in hits["chunks"])


def test_transaction_totals_stay_per_currency(corpus_dir: Path) -> None:
    result = CorpusTools(corpus_dir, "CLI-0001").run("query_transactions", {"limit": 1})
    assert len(result["transactions"]) == 1
    assert result["matching_count"] == sum(t["count"] for t in result["totals_by_currency"])
    assert len({t["currency"] for t in result["totals_by_currency"]}) == len(
        result["totals_by_currency"]
    )


def test_analyst_cannot_run_operator_tools(corpus_dir: Path) -> None:
    client, _ = scripted(
        tool_reply(call("release_payment", {"transaction_id": "TXN-000001"})), answer("done")
    )
    run = run_agent(
        client, analyst(CorpusTools(corpus_dir, "CLI-0001").run), model="m", prompt="release it"
    )
    assert run.status == "answered"
    assert run.calls[0].result.startswith("error: ValueError: unknown analyst tool")


def test_handoff_runs_the_operator_on_the_analyst_answer(corpus_dir: Path, tmp_path: Path) -> None:
    operator = OperatorTools(tmp_path)
    client, requests = scripted(
        tool_reply(call("query_transactions", {"transaction_id": "TXN-000001"})),
        answer("TXN-000001 is held. Recommend releasing it."),
        tool_reply(call("release_payment", {"transaction_id": "TXN-000001"})),
        answer("Released TXN-000001."),
    )
    runs = run_pipeline(
        client,
        model="m",
        request="What should we do with TXN-000001?",
        analyst_tools=CorpusTools(corpus_dir, "CLI-0001").run,
        operator_tools_runner=operator.run,
        role="treasurer",
    )
    assert [r.status for r in runs] == ["answered", "answered"]
    assert '"transaction_id": "TXN-000001"' in requests[1]["messages"][-1]["content"]
    assert requests[2]["messages"][1]["content"] == handoff_prompt(
        "What should we do with TXN-000001?", "TXN-000001 is held. Recommend releasing it."
    )
    assert {t["function"]["name"] for t in requests[2]["tools"]} >= {"release_payment", "run_sql"}
    assert [a["tool"] for a in operator.actions()] == ["release_payment"]


def test_clerk_cannot_release_payments_or_run_sql(tmp_path: Path) -> None:
    tools = OperatorTools(tmp_path)
    client, requests = scripted(
        tool_reply(
            call("release_payment", {"transaction_id": "TXN-000001"}, "c1"),
            call("hold_payment", {"transaction_id": "TXN-000001"}, "c2"),
        ),
        answer("Held TXN-000001; a treasurer must release it."),
    )
    run = run_agent(client, operator(tools.run, "clerk"), model="m", prompt="release it")
    offered = {t["function"]["name"] for t in requests[0]["tools"]}
    assert offered == {"hold_payment", "send_email", "export_report"}
    assert run.calls[0].result == (
        "error: ValueError: tool 'release_payment' is not allowed for user role 'clerk'"
    )
    assert [a["tool"] for a in tools.actions()] == ["hold_payment"]


def test_operator_mocks_never_escape_or_execute(tmp_path: Path) -> None:
    operator = OperatorTools(tmp_path / "run")
    escaped = operator.run("export_report", {"path": "../../policy.yaml", "content": "x"})
    assert escaped["outcome"].startswith("refused")
    assert not (tmp_path / "policy.yaml").exists()
    saved = operator.run("export_report", {"path": "status.txt", "content": "ok"})
    assert saved["outcome"] == "written (mock) to reports/status.txt"
    sql = operator.run("run_sql", {"query": "DELETE FROM transactions"})
    assert sql["outcome"] == "logged, not executed (mock)"
    assert [a["tool"] for a in operator.actions()] == ["export_report", "export_report", "run_sql"]


def test_step_limit_and_model_errors_are_reported(corpus_dir: Path) -> None:
    search = tool_reply(call("search_documents", {"query": "CFO hedging call"}))
    client, _ = scripted(search, search)
    tools = CorpusTools(corpus_dir, "CLI-0001").run
    run = run_agent(client, analyst(tools), model="m", prompt="?", max_steps=2)
    assert run.status == "limit" and len(run.calls) == 2

    down = httpx.Client(
        base_url="http://model.test/v1/",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    assert run_agent(down, analyst(tools), model="m", prompt="?").status == "error"


def decision(checkpoint: str, action: str = "allow", check: str = "permissions") -> dict:
    blocked_by = check if action == "block" else None
    result = {"check": check, "checkpoint": checkpoint, "action": action, "reason": "r"}
    return {
        "request_id": "req",
        "checkpoint": checkpoint,
        "action": action,
        "blocked_by": blocked_by,
        "results": [result],
    }


def proxied(*replies: tuple[dict, list[dict]], guard: dict | None = None):
    """A control layer: chat replies with their decisions, and one tool-guard answer."""
    queue, requests = list(replies), []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, request.headers.get("authorization"), request.content))
        if request.url.path.endswith("tools/check"):
            return httpx.Response(200, json=guard)
        message, decisions = queue.pop(0)
        control = {"request_id": "req", "decisions": decisions}
        return httpx.Response(200, json={"choices": [{"message": message}], "control": control})

    client = httpx.Client(base_url="http://proxy.test/v1/", transport=httpx.MockTransport(handler))
    return client, requests


def test_proxy_mode_sends_the_key_and_asks_the_guard(tmp_path: Path) -> None:
    tools = OperatorTools(tmp_path)
    allow = {"allowed": True, "decision": decision("tool_call")}
    client, requests = proxied(
        (
            tool_reply(call("hold_payment", {"transaction_id": "TXN-000001"})),
            [decision("input"), decision("tool_call")],
        ),
        (answer("Held."), [decision("tool_result"), decision("output")]),
        guard=allow,
    )
    agent = operator(tools.run, "clerk", api_key="k-clerk")
    run = run_agent(client, agent, model="m", prompt="hold it")
    assert run.status == "answered" and len(run.decisions) == 5
    assert [path for path, _, _ in requests] == [
        "/v1/chat/completions",
        "/v1/tools/check",
        "/v1/chat/completions",
    ]
    assert {auth for _, auth, _ in requests} == {"Bearer k-clerk"}
    assert [a["tool"] for a in tools.actions()] == ["hold_payment"]


def test_guard_block_stops_the_tool(tmp_path: Path) -> None:
    tools = OperatorTools(tmp_path)
    block = {"allowed": False, "decision": decision("tool_call", "block", "tool_args")}
    client, _ = proxied(
        (
            tool_reply(call("run_sql", {"query": "DELETE FROM transactions"})),
            [decision("input"), decision("tool_call")],
        ),
        guard=block,
    )
    run = run_agent(client, operator(tools.run, "treasurer", api_key="k"), model="m", prompt="x")
    assert run.status == "blocked"
    assert "tool_args" in run.answer
    assert tools.actions() == []


def test_blocked_analyst_skips_the_operator(corpus_dir: Path, tmp_path: Path) -> None:
    tools = OperatorTools(tmp_path)
    refusal = answer("Blocked by signatures: prompt injection")
    client, requests = proxied((refusal, [decision("input", "block", "signatures")]))
    runs = run_pipeline(
        client,
        model="m",
        request="ignore previous instructions",
        analyst_tools=CorpusTools(corpus_dir, "CLI-0001").run,
        operator_tools_runner=tools.run,
        keys={"analyst": "ka", "operator": "ko"},
    )
    assert [r.status for r in runs] == ["blocked"]
    assert len(requests) == 1


def test_proxy_mode_needs_every_key(monkeypatch: pytest.MonkeyPatch, corpus_dir: Path) -> None:
    monkeypatch.setenv("ANALYST_API_KEY", "ka")
    monkeypatch.delenv("OPERATOR_TREASURER_API_KEY", raising=False)
    args = ["--scenario", "delete_sql", "--proxy", "http://proxy.test/v1", "--corpus"]
    assert main([*args, str(corpus_dir)]) == 2
