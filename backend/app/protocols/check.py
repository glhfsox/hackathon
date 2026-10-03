"""The one interface every check implements (constitution III). One check, one module.

A check reports its raw opinion in `verdict`; the pipeline maps it to the final `action` (a finding
blocks, a redaction is applied, an error blocks). A check reads only its own settings and never
imports another check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.budget import UsageLedger
from app.models import CanonicalRequest, Checkpoint, CheckResult
from app.protocols.judge import Judge


@dataclass(frozen=True)
class Signature:
    id: str
    pattern: re.Pattern[str]
    category: str = "other"
    description: str = ""


@dataclass
class CheckContext:
    """Everything a check may use besides the request and its own policy section.

    Checks never import other checks or the policy store; the pipeline fills this in.
    """

    ledger: UsageLedger | None = None
    # None means the feed never loaded: the signatures check must report an error, not allow.
    signatures: list[Signature] | None = None
    jev_threshold: float = 1.0
    judge: Judge | None = None


class Check(Protocol):
    id: str
    cost_rank: int
    checkpoints: frozenset[Checkpoint]

    async def run(
        self, request: CanonicalRequest, settings: dict[str, Any], ctx: CheckContext
    ) -> CheckResult:
        """Return the raw verdict (allow | redact | block | error); the pipeline maps the mode."""
        ...
