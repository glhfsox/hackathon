"""Usage ledger: the only request-spanning state besides the policy (docs/architecture.md §3)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime


class UsageLedger:
    """In-memory per-caller counters. The clock is injectable so tests control time."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._tokens: dict[tuple[str, str], int] = defaultdict(int)
        self._cost: dict[tuple[str, str], float] = defaultdict(float)

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%d")

    def _prune(self, caller_id: str) -> deque[float]:
        window = self._requests[caller_id]
        cutoff = self._clock() - 60.0
        while window and window[0] <= cutoff:
            window.popleft()
        return window

    def record_request(self, caller_id: str) -> None:
        # Prune here as well: a caller without a per-minute limit is never read, only written.
        self._prune(caller_id).append(self._clock())

    def record_usage(self, caller_id: str, tokens: int, cost: float) -> None:
        key = (caller_id, self._day())
        self._tokens[key] += tokens
        self._cost[key] += cost

    def requests_last_minute(self, caller_id: str) -> int:
        return len(self._prune(caller_id))

    def tokens_today(self, caller_id: str) -> int:
        return self._tokens.get((caller_id, self._day()), 0)

    def cost_today(self, caller_id: str) -> float:
        return self._cost.get((caller_id, self._day()), 0.0)
