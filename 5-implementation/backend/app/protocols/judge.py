"""The AI decision maker interface. Jev with its local fallback implements it."""

from __future__ import annotations

from typing import Protocol

from app.models import JudgeInput, JudgeVerdict


class JudgeUnavailable(Exception):
    """Neither Jev nor the fallback produced a verdict. The pipeline fails closed."""


class Judge(Protocol):
    async def judge(self, inp: JudgeInput) -> JudgeVerdict:
        """Raise JudgeUnavailable when no decision maker answers."""
        ...


class JudgeHealth(Protocol):
    """Reachability of the decision makers, for GET /api/health."""

    async def health(self) -> dict[str, str]:
        """{"jev": "up" | "down", "fallback": "up" | "down"}"""
        ...
