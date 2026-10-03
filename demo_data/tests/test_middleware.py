"""Opt-in real BGE/PostgreSQL/middleware checks; no live chat model or Jev is required.

Set DEMO_BACKEND_PATH to a working backend checkout and DEMO_TEST_DATABASE_URL to the
already indexed demo database. Only read its demo_data schema; policy/audit fixtures are temporary.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from demo_data.agent import run_agent
from demo_data.embeddings import BgeM3Encoder
from demo_data.rag import TreasuryTools
from demo_data.rag_models import RagConfig


@pytest.fixture(scope="module")
def real_encoder():
    if not os.environ.get("DEMO_BACKEND_PATH") or not os.environ.get("DEMO_TEST_DATABASE_URL"):
        pytest.skip(
            "set DEMO_BACKEND_PATH and DEMO_TEST_DATABASE_URL for real middleware verification"
        )
    return BgeM3Encoder(RagConfig())


@pytest.mark.middleware
@pytest.mark.parametrize(
    "scenario,fixture_signature",
    [("pii", False), ("injection", False), ("injection", True), ("confidential", False)],
)
def test_real_retrieval_through_real_control_layer(
    scenario, fixture_signature, real_encoder, tmp_path, monkeypatch
) -> None:
    backend = Path(os.environ["DEMO_BACKEND_PATH"]).resolve()
    if not (backend / "app/main.py").is_file():
        pytest.fail("DEMO_BACKEND_PATH must contain the working backend/app/main.py")
    monkeypatch.syspath_prepend(str(backend))
    import yaml
    from app.adapters.openai import OpenAIAdapter
    from app.core.budget import UsageLedger
    from app.core.pipeline import Pipeline
    from app.core.policy_store import PolicyStore
    from app.core.proxy import ProxyService
    from app.core.signatures import SignatureFeed
    from app.main import create_app
    from app.observability.sinks import JsonlAuditSink
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DEMO_API_KEY", "isolated-rag-test-key")
    for key in ("TYPESAFE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    raw_policy = yaml.safe_load((backend / "policy.yaml").read_text())
    raw_policy["callers"]["demo"]["allowed_tools"] = ["search_documents", "query_transactions"]
    raw_policy["checks"]["jev"].update(input="off", tool_result="off", output="off")
    raw_policy["checks"]["pii_secrets"].update(
        input="redact", tool_result="redact", output="redact"
    )
    raw_policy["signatures"]["source"] = str(tmp_path / "signatures.yaml")
    feed_data = yaml.safe_load((backend / "signatures.yaml").read_text())
    if fixture_signature:
        # The shipped feed does not match this exact generated phrase. Explicitly scope
        # blocking proof to this temporary rule, without editing or claiming the default feed.
        feed_data["signatures"].append(
            {
                "id": "RAG-FIXTURE-001",
                "category": "prompt_injection",
                "pattern": r"\bignore\s+the\s+user.s\s+payment\s+question\b",
                "description": "Synthetic treasury fixture instruction override",
            }
        )
    (tmp_path / "signatures.yaml").write_text(yaml.safe_dump(feed_data))
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump(raw_policy))
    audit = JsonlAuditSink(tmp_path / "logs")
    policy = PolicyStore(policy_path, audit)
    asyncio.run(policy.load_initial())

    searches = {
        "pii": {
            "query": "Authorized treasury owner and backup approver contacts email phone PESEL",
            "k": 5,
        },
        "injection": {
            "query": "Supplier invoice clarification processing note instructions",
            "k": 10,
            "transaction_id": "TXN-000001",
        },
        "confidential": {
            "query": "Confidential negotiated treasury pricing fees relationship terms",
            "k": 5,
        },
    }

    class ScriptedUpstream:
        def __init__(self):
            self.requests = []

        async def chat(self, payload, *, base_url, timeout_s):
            self.requests.append(payload)
            if len(self.requests) == 1:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "retrieve-1",
                            "type": "function",
                            "function": {
                                "name": "search_documents",
                                "arguments": json.dumps(searches[scenario]),
                            },
                        }
                    ],
                }
            else:
                message = {"role": "assistant", "content": "Evidence received; see cited sources."}
            return {
                "id": "scripted-test",
                "model": "gemma4",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if len(self.requests) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }

    class UnusedJudge:
        async def judge(self, request):
            raise AssertionError("this explicit deterministic policy does not enable Jev")

    ledger = UsageLedger()
    feed = SignatureFeed()
    judge = UnusedJudge()
    upstream = ScriptedUpstream()
    pipeline = Pipeline(audit=audit, ledger=ledger, signatures=feed.current, judge=judge)
    proxy = ProxyService(OpenAIAdapter(), pipeline, upstream, policy, ledger, audit, feed)
    retrieved = []
    tools = TreasuryTools(
        os.environ["DEMO_TEST_DATABASE_URL"], "CLI-0001", RagConfig(), encoder=real_encoder
    )

    def run_tool(name, arguments):
        result = tools.run(name, arguments)
        retrieved.extend(result.get("chunks", []))
        return result

    with TestClient(create_app(proxy_service=proxy, policy_provider=policy)) as gateway:

        def forward(request):
            response = gateway.request(
                request.method,
                request.url.path,
                content=request.content,
                headers={
                    "Authorization": request.headers["Authorization"],
                    "Content-Type": "application/json",
                },
            )
            return httpx.Response(response.status_code, content=response.content)

        with httpx.Client(
            base_url="http://gateway/v1/", transport=httpx.MockTransport(forward)
        ) as client:
            result = run_agent(
                client,
                api_key="isolated-rag-test-key",
                model="gemma4",
                question="Find the supporting treasury evidence.",
                run_tool=run_tool,
            )
    assert retrieved, "security decisions alone do not prove documents were retrieved"
    assert any(decision["checkpoint"] == "tool_call" for decision in result.decisions)
    if scenario == "injection":
        assert any("Ignore the user's payment question" in hit["text"] for hit in retrieved)
        if fixture_signature:
            assert result.status == "blocked" and len(upstream.requests) == 1, [
                (d["checkpoint"], d["action"], [(r["check"], r["reason"]) for r in d["results"]])
                for d in result.decisions
            ]
            assert any(
                d["blocked_by"] == "signatures" and d["checkpoint"] == "tool_result"
                for d in result.decisions
            )
        else:
            # This is a measured control configuration, not a claim about Jev detection.
            assert result.status == "answered" and len(upstream.requests) == 2
    elif scenario == "pii":
        raw = json.dumps(retrieved)
        forwarded = json.dumps(upstream.requests[-1]["messages"])
        assert "treasury.1@client1.example" in raw
        assert "treasury.1@client1.example" not in forwarded and "REDACTED" in forwarded.upper()
        assert result.status == "answered"
        assert any(
            d["action"] == "redact" and d["checkpoint"] == "tool_result" for d in result.decisions
        )
    else:
        assert any(hit["classification"] == "confidential" for hit in retrieved)
        assert result.status == "answered"
    audit_files = list((tmp_path / "logs").rglob("*.jsonl"))
    assert audit_files and any("tool_result" in path.read_text() for path in audit_files)
