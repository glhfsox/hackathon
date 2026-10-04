import json

import httpx
import pytest
from demo_data.agent import run_agent, tool_result_content


def decision(checkpoint="input", action="allow"):
    return {
        "request_id": "test-request",
        "checkpoint": checkpoint,
        "action": action,
        "blocked_by": "signatures" if action == "block" else None,
        "results": [],
    }


def reply(tool=False, blocked=False, incoming="input"):
    message = {
        "role": "assistant",
        "content": "Blocked by signatures: injected" if blocked else "Payment held.",
    }
    if tool and not blocked:
        message.update(
            content=None,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "search_documents",
                        "arguments": json.dumps({"query": "invoice", "k": 5}),
                    },
                }
            ],
        )
    return {
        "choices": [{"message": message}],
        "control": {
            "request_id": "test-request",
            "decisions": [
                decision(incoming, "block" if blocked else "allow"),
                *([] if blocked else [decision("tool_call" if tool else "output")]),
            ],
        },
    }


def test_agent_guards_exact_call_and_resends_tool_results() -> None:
    seen = []
    executions = []

    def handler(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body, request.headers.get("Authorization")))
        if request.url.path.endswith("tools/check"):
            return httpx.Response(200, json={"allowed": True, "decision": decision("tool_call")})
        continued = any(m["role"] == "tool" for m in body["messages"])
        return httpx.Response(
            200, json=reply(tool=not continued, incoming="tool_result" if continued else "input")
        )

    def tool(name, args):
        executions.append((name, args))
        return {"chunks": [{"document_id": "DOC-00002", "text": "Invoice mismatch."}]}

    with httpx.Client(
        base_url="http://control/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = run_agent(
            client,
            api_key="test-key",
            model="configured-model",
            question="Why held?",
            run_tool=tool,
        )
    assert result.status == "answered" and result.source_ids == ["DOC-00002"]
    assert executions == [("search_documents", {"query": "invoice", "k": 5})]
    assert [path for path, _, _ in seen] == [
        "/v1/chat/completions",
        "/v1/tools/check",
        "/v1/chat/completions",
    ]
    assert all(auth == "Bearer test-key" for _, _, auth in seen)
    assert seen[1][1]["tool_call"] == {
        "id": "call-1",
        "name": "search_documents",
        "arguments": executions[0][1],
    }
    assert all(
        "function" not in call
        for message in seen[1][1]["messages"]
        for call in message["tool_calls"]
    )
    assert seen[-1][1]["messages"][-1]["role"] == "tool"
    assert "Invoice mismatch" in seen[-1][1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "guard",
    [
        {"allowed": False, "decision": decision("tool_call", "block")},
        {"allowed": True, "decision": decision("tool_call", "block")},
        {"allowed": True, "decision": decision("tool_call", "redact")},
        {"allowed": "true", "decision": decision("tool_call")},
        {"allowed": True},
        {"allowed": True, "decision": decision("input")},
        None,
    ],
)
def test_denied_invalid_or_unavailable_guard_never_executes(guard) -> None:
    executions = []

    def handler(request):
        if request.url.path.endswith("tools/check"):
            if guard is None:
                raise httpx.ConnectError("cannot connect", request=request)
            return httpx.Response(200, json=guard)
        return httpx.Response(200, json=reply(tool=True))

    with httpx.Client(
        base_url="http://control/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = run_agent(
            client,
            api_key="k",
            model="m",
            question="q",
            run_tool=lambda *args: executions.append(args),
        )
    assert result.status in ("blocked", "error") and executions == []


@pytest.mark.parametrize(
    "body",
    [
        reply(tool=True, blocked=True),
        {"choices": [{"message": {"role": "assistant", "content": "raw upstream"}}]},
    ],
)
def test_blocked_or_unchecked_proxy_reply_never_executes(body) -> None:
    executions = []
    with httpx.Client(
        base_url="http://control/v1/",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    ) as client:
        result = run_agent(
            client,
            api_key="k",
            model="m",
            question="q",
            run_tool=lambda *args: executions.append(args),
        )
    assert result.status in ("blocked", "error") and not executions


def test_extra_tool_scope_is_rejected_and_step_limit_is_explicit() -> None:
    executions = []
    body = reply(tool=True)
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(
        {"query": "x", "client_id": "CLI-9999"}
    )
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=body)

    with httpx.Client(
        base_url="http://control/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = run_agent(
            client,
            api_key="k",
            model="m",
            question="q",
            max_steps=1,
            run_tool=lambda *args: executions.append(args),
        )
    assert not executions and result.status == "limit"
    assert seen == ["/v1/chat/completions"]


def test_document_tool_content_preserves_verbatim_lines_and_classification() -> None:
    text = "## Processing note\n\nIgnore this instruction. Łódź.\n"
    content = tool_result_content(
        {"chunks": [{"document_id": "DOC-00008", "classification": "internal", "text": text}]}
    )
    assert content.endswith(text) and "\\n" not in content
    assert '"classification": "internal"' in content
