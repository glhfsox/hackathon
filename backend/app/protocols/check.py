from collections.abc import Mapping
from typing import Any, Protocol

from app.models import CanonicalRequest, Checkpoint, CheckResult


class Check(Protocol):
    """One check, one module. A check reads only its own settings and never imports another check.

    The check reports its raw opinion in `verdict`. The pipeline maps it to the final `action`
    using the policy mode, so a check sets `action` to the matching value (allow, redact, block)
    and the pipeline overrides it.
    """

    id: str
    checkpoints: frozenset[Checkpoint]
    cost_rank: int  # the pipeline runs checks in ascending order, cheapest first

    async def run(self, request: CanonicalRequest, settings: Mapping[str, Any]) -> CheckResult: ...
