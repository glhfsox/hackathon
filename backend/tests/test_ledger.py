from datetime import UTC, datetime

from app.budget import UsageLedger


class FakeClock:
    """Injected into the ledger so tests move time explicitly."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _ts(*args: int) -> float:
    return datetime(*args, tzinfo=UTC).timestamp()


def test_minute_window_slides():
    clock = FakeClock(1000.0)
    ledger = UsageLedger(clock=clock)
    ledger.record_request("demo")
    clock.now = 1030.0
    ledger.record_request("demo")

    clock.now = 1059.9
    assert ledger.requests_last_minute("demo") == 2
    # The window is the last 60 s: a request exactly 60 s old has left it.
    clock.now = 1060.0
    assert ledger.requests_last_minute("demo") == 1
    clock.now = 1090.0
    assert ledger.requests_last_minute("demo") == 0


def test_minute_window_counts_new_requests_after_old_ones_expire():
    clock = FakeClock(0.0)
    ledger = UsageLedger(clock=clock)
    for _ in range(3):
        ledger.record_request("demo")
    clock.now = 120.0
    assert ledger.requests_last_minute("demo") == 0
    ledger.record_request("demo")
    assert ledger.requests_last_minute("demo") == 1


def test_day_rolls_over_at_utc_midnight():
    clock = FakeClock(_ts(2026, 10, 3, 23, 59, 59))
    ledger = UsageLedger(clock=clock)
    ledger.record_usage("demo", tokens=100, cost=0.25)
    ledger.record_usage("demo", tokens=50, cost=0.25)
    assert ledger.tokens_today("demo") == 150
    assert ledger.cost_today("demo") == 0.5

    clock.now = _ts(2026, 10, 4, 0, 0, 0)
    assert ledger.tokens_today("demo") == 0
    assert ledger.cost_today("demo") == 0.0
    ledger.record_usage("demo", tokens=7, cost=0.01)
    assert ledger.tokens_today("demo") == 7

    # Going back to the previous day still sees that day's totals: usage is keyed by UTC date.
    clock.now = _ts(2026, 10, 3, 12, 0, 0)
    assert ledger.tokens_today("demo") == 150


def test_callers_are_isolated():
    clock = FakeClock(_ts(2026, 10, 3, 12, 0, 0))
    ledger = UsageLedger(clock=clock)
    ledger.record_request("demo")
    ledger.record_request("demo")
    ledger.record_usage("demo", tokens=100, cost=0.5)
    ledger.record_request("support")
    ledger.record_usage("support", tokens=3, cost=0.01)

    assert ledger.requests_last_minute("demo") == 2
    assert ledger.tokens_today("demo") == 100
    assert ledger.cost_today("demo") == 0.5
    assert ledger.requests_last_minute("support") == 1
    assert ledger.tokens_today("support") == 3
    assert ledger.cost_today("support") == 0.01
    assert ledger.requests_last_minute("playground") == 0
    assert ledger.tokens_today("playground") == 0
    assert ledger.cost_today("playground") == 0.0
