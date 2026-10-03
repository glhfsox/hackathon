"""Fixtures for the demo tests. Nothing here needs Ollama, Jev or the network.

`upstream` (a scripted model), `gateway` (the real control layer in a TestClient) and the
helpers they use come from tests/conftest.py.

- `bridged_client`: an OpenAI client wired to that TestClient. The SDK speaks httpx2, which
  respx does not patch, so its transport hands each request to the TestClient instead.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from demo import tools
from tests.conftest import token


@pytest.fixture(autouse=True)
def demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never the real Jev or Langfuse: the judge is the (mocked) local fallback.
    for name in ("TYPESAFE_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tools, "WORKSPACE", tmp_path / "workspace")
    return tools.init_workspace()


def bridged_client(gateway: TestClient, roles: tuple[str, ...] = ("developer",)) -> openai.OpenAI:
    """An OpenAI client that reaches `gateway` with its own token: one per agent, by role."""

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
        api_key=token(roles[0], roles),
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(forward)),
    )
