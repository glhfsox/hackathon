"""Fixtures for the demo tests. Nothing here needs Ollama, Jev or the network.

- `upstream`: a scripted OpenAI-compatible model behind respx. The Jev fallback judge calls the
  same Ollama URL, so judge requests (they ask for `response_format`) get a benign verdict.
- `gateway`: the demo gateway (create_app) in a FastAPI TestClient, on a temp policy copy.
- `bridged_client`: an OpenAI client wired to that TestClient. The SDK speaks httpx2, which
  respx does not patch, so its transport hands each request to the TestClient instead.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import httpx2
import openai
import pytest
import respx
from fastapi.testclient import TestClient

from demo import tools
from demo.gateway import create_app
from demo.run import copy_policy

UPSTREAM_URL = "http://localhost:11434/v1/chat/completions"
MODELS_URL = "http://localhost:11434/v1/models"
DEMO_KEY = "test-demo-key"  # tests/conftest.py sets DEMO_API_KEY to this
ORCHESTRATOR_KEY = "test-orchestrator-key"
AUTH = {"Authorization": f"Bearer {DEMO_KEY}"}


def completion(
    content: str | None = None,
    tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    *,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
) -> dict[str, Any]:
    """An upstream chat.completion body, Ollama style."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(tool_calls)
        ]
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gemma4",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class FakeUpstream:
    """Answers model calls from a script, in order, and records what the model received."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.replies: deque[dict[str, Any] | httpx.Response | Exception] = deque()
        self.requests: list[dict[str, Any]] = []  # bodies the model received
        self.judged: list[dict[str, Any]] = []  # bodies the Jev fallback judge received

    def script(self, *replies: dict[str, Any] | httpx.Response | Exception) -> None:
        self.replies.extend(replies)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "response_format" in body:  # app/jev.py fallback judge
            self.judged.append(body)
            verdict = {"score": 0.0, "reason": "fake judge: benign", "categories": []}
            return httpx.Response(200, json=completion(json.dumps(verdict)))
        self.requests.append(body)
        if not self.replies:
            raise AssertionError("the upstream model was called more often than scripted")
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


@pytest.fixture(autouse=True)
def demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", ORCHESTRATOR_KEY)
    # Never the real Jev or Langfuse: the judge is the (mocked) local fallback.
    for name in ("TYPESAFE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path / "workspace")
    return tools.init_workspace()


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    return copy_policy(tmp_path)


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def upstream() -> Iterator[FakeUpstream]:
    with respx.mock(assert_all_called=False) as router:
        fake = FakeUpstream(router)
        router.post(UPSTREAM_URL).mock(side_effect=fake.handle)
        router.get(MODELS_URL).respond(200, json={"object": "list", "data": []})
        yield fake


@pytest.fixture
def gateway(policy_path: Path, logs_dir: Path, upstream: FakeUpstream) -> Iterator[TestClient]:
    with TestClient(create_app(policy_path, logs_dir)) as client:
        yield client


def bridged_client(gateway: TestClient, api_key: str) -> openai.OpenAI:
    def forward(request: httpx2.Request) -> httpx2.Response:
        resp = gateway.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers={
                "authorization": request.headers["authorization"],
                "content-type": "application/json",
            },
        )
        return httpx2.Response(
            resp.status_code,
            headers={"content-type": resp.headers.get("content-type", "application/json")},
            content=resp.content,
        )

    return openai.OpenAI(
        base_url="http://gateway.test/v1",
        api_key=api_key,
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(forward)),
    )


def wait_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s}s")
        time.sleep(0.1)
