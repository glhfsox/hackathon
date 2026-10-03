"""Fixtures shared by every backend test. Tests never call the real Jev: use FakeJudge."""

from __future__ import annotations

from typing import Literal

import pytest

from app.audit import MemoryAuditSink
from app.checks.base import JudgeUnavailable
from app.models import JudgeInput, JudgeVerdict

TEST_API_KEYS = {
    "DEMO_API_KEY": "test-demo-key",
    "SUPPORT_API_KEY": "test-support-key",
    "PLAYGROUND_API_KEY": "test-playground-key",
}


@pytest.fixture(autouse=True)
def api_keys(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Known caller keys for every test, so key resolution never depends on the developer's env."""
    for name, value in TEST_API_KEYS.items():
        monkeypatch.setenv(name, value)
    return dict(TEST_API_KEYS)


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
