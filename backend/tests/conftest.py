"""Fixtures shared by every backend test. Tests never call the real Jev: use FakeJudge.

For tests of the whole application:
- `upstream`: a scripted OpenAI-compatible model behind respx. The Jev fallback judge calls the
  same Ollama URL, so judge requests (they ask for `response_format`) get a benign verdict.
- `gateway`: the real application (app.main.create_app) in a FastAPI TestClient, on a temp copy
  of policy.yaml and its signature feed, with the audit log in a temp folder.
"""

from __future__ import annotations

import json
import shutil
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.checks.base import JudgeUnavailable
from app.main import create_app
from app.models import JudgeInput, JudgeVerdict
from app.observability.sinks import MemoryAuditSink

BACKEND_DIR = Path(__file__).resolve().parents[1]
UPSTREAM_URL = "http://localhost:11434/v1/chat/completions"
MODELS_URL = "http://localhost:11434/v1/models"


@pytest.fixture
def audit_sink() -> MemoryAuditSink:
    return MemoryAuditSink()


class FakeJudge:
    """Stand-in for the Jev client: a fixed verdict, or JudgeUnavailable when `unavailable`.

    Every JudgeInput is kept in `calls`, so tests can assert what would have left the system.
    """

    def __init__(
        self,
        score: float = 0.0,
        reason: str = "fake judge verdict",
        categories: list[str] | None = None,
        *,
        decided_by: Literal["jev", "fallback"] = "jev",
        unavailable: bool = False,
    ) -> None:
        self.verdict = JudgeVerdict(
            score=score, reason=reason, categories=categories or [], decided_by=decided_by
        )
        self.unavailable = unavailable
        self.calls: list[JudgeInput] = []

    async def judge(self, inp: JudgeInput) -> JudgeVerdict:
        self.calls.append(inp)
        if self.unavailable:
            raise JudgeUnavailable("fake judge configured as unavailable")
        return self.verdict


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
        if "response_format" in body:  # app/core/jev.py fallback judge
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


def wait_until(condition: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout_s}s")
        time.sleep(0.1)


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    """A temp copy of policy.yaml and its signature feed (a relative `source`), safe to edit."""
    for name in ("policy.yaml", "signatures.yaml"):
        shutil.copy2(BACKEND_DIR / name, tmp_path / name)
    return tmp_path / "policy.yaml"


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
def gateway(
    policy_path: Path, logs_dir: Path, upstream: FakeUpstream, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # Never the real Jev or Langfuse: the judge is the (mocked) local fallback.
    for name in ("TYPESAFE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(policy_path=policy_path, logs_dir=logs_dir)) as client:
        yield client
