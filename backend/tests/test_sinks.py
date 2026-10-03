import asyncio
import json
import logging
import threading
from pathlib import Path

import pytest

from app.audit import MemoryAuditSink
from app.models import AuditRecord
from app.sinks import COLUMNS, FanoutAuditSink, JsonlAuditSink, audit_row, read_jsonl


def rec(ts: str = "2026-10-03T12:34:56.789+00:00", **kw) -> AuditRecord:
    fields = {
        "request_id": "req-1",
        "caller_id": "demo",
        "model": "gemma4",
        "checkpoint": "input",
        "check": "pii_secrets",
        "action": "allow",
        "reason": "nothing found",
        "latency_ms": 1.25,
        "policy_version": "0.1+abcd1234",
    }
    fields.update(kw)
    return AuditRecord(ts=ts, **fields)


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- JsonlAuditSink -------------------------------------------------------------------------


async def test_rows_are_flat_json_in_column_order(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path / "logs")
    await sink.write(rec(action="redact", reason="redacted 1 EMAIL", tokens=12, cost=0.5))

    [row] = lines(tmp_path / "logs" / "audit-2026-10-03.jsonl")
    assert list(row) == list(COLUMNS)
    assert all(not isinstance(v, dict | list) for v in row.values())
    assert row["id"] == 1
    assert row["checkpoint"] == "input"
    assert row["action"] == "redact"
    assert row["decided_by"] == "rules"
    assert row["tokens"] == 12
    assert row["cost"] == 0.5


AGENT_COLUMNS = {
    "conversation_id": "3f2a9c0d1e4b5a67",
    "step": 1,
    "messages": 5,
    "tool_calls": 2,
    "tools": "query_customers,run_shell",
    "tool_calls_total": 3,
    "prompt_tokens": 900,
    "completion_tokens": 300,
    "upstream_latency_ms": 250.0,
    "overhead_ms": 1.25,
    "blocked_by": "tool_args",
}


async def test_turn_summary_rows_carry_the_agent_columns(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    await sink.write(rec())
    await sink.write(
        rec(
            check="turn_summary",
            checkpoint="tool_call",
            action="block",
            reason="blocked by tool_args",
            tokens=1200,
            cost=0.36,
            **AGENT_COLUMNS,
        )
    )

    check_row, summary = lines(tmp_path / "audit-2026-10-03.jsonl")
    assert set(AGENT_COLUMNS) <= set(COLUMNS)
    assert list(summary) == list(check_row) == list(COLUMNS)
    assert {k: summary[k] for k in AGENT_COLUMNS} == AGENT_COLUMNS
    assert (summary["tokens"], summary["cost"]) == (1200, 0.36)
    assert summary["is_system_event"] is True
    # Check rows leave the agent columns empty.
    assert all(check_row[k] is None for k in AGENT_COLUMNS)
    assert check_row["is_system_event"] is False
    # And they survive the round trip back into AuditRecords.
    assert read_jsonl(tmp_path)[1].model_dump(include=set(AGENT_COLUMNS)) == AGENT_COLUMNS


async def test_sink_assigns_id_on_the_record_like_memory_sink(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    first, second = rec(), rec()
    await sink.write(first)
    await sink.write(second)
    assert (first.id, second.id) == (1, 2)


def test_derived_columns() -> None:
    row = audit_row(rec(ts="2026-10-03T09:05:59+02:00", action="block"))
    # Converted to UTC: 07:05.
    assert row["date"] == "2026-10-03"
    assert row["hour"] == 7
    assert row["minute"] == "2026-10-03T07:05:00Z"
    assert (row["is_block"], row["is_redact"], row["is_flag"]) == (True, False, False)
    assert row["is_system_event"] is False
    assert row["category"] is None

    assert audit_row(rec(action="redact"))["is_redact"] is True
    assert audit_row(rec(action="flag"))["is_flag"] is True
    # A naive timestamp is read as UTC.
    assert audit_row(rec(ts="2026-10-03T23:10:00"))["hour"] == 23


def test_system_event_rule_matches_metrics() -> None:
    loaded = rec(request_id=None, checkpoint=None, check="policy_loaded", reason="loaded")
    assert audit_row(loaded)["is_system_event"] is True
    # A known system-event id counts even with a checkpoint.
    assert audit_row(rec(check="upstream_unavailable"))["is_system_event"] is True
    # No checkpoint means system event, whatever the id.
    assert audit_row(rec(checkpoint=None, check="something_new"))["is_system_event"] is True


@pytest.mark.parametrize(
    ("check", "reason", "category"),
    [
        (
            "signatures",
            "signature PI-001 (prompt_injection): instruction override",
            "prompt_injection",
        ),
        ("signatures", "no match among 12 signatures", None),
        (
            "jev",
            "hidden instruction in tool output [prompt_injection, exfiltration]",
            "prompt_injection",
        ),
        ("jev", "looks benign", None),
        ("pii_secrets", "redacted 1 EMAIL [x]", None),
    ],
)
def test_category_is_best_effort(check: str, reason: str, category: str | None) -> None:
    assert audit_row(rec(check=check, reason=reason))["category"] == category


async def test_day_rollover_creates_a_second_file(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    await sink.write(rec(ts="2026-10-03T23:59:59+00:00"))
    await sink.write(rec(ts="2026-10-04T00:00:01+00:00"))
    # 01:30 at +02:00 is still 2026-10-03 in UTC.
    await sink.write(rec(ts="2026-10-04T01:30:00+02:00"))

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "audit-2026-10-03.jsonl",
        "audit-2026-10-04.jsonl",
    ]
    assert [r["id"] for r in lines(tmp_path / "audit-2026-10-03.jsonl")] == [1, 3]
    assert [r["id"] for r in lines(tmp_path / "audit-2026-10-04.jsonl")] == [2]


async def test_ids_continue_after_restart(tmp_path: Path) -> None:
    first = JsonlAuditSink(tmp_path)
    for _ in range(3):
        await first.write(rec())

    second = JsonlAuditSink(tmp_path)
    await second.write(rec())

    assert [r["id"] for r in lines(tmp_path / "audit-2026-10-03.jsonl")] == [1, 2, 3, 4]


async def test_restart_after_torn_line_keeps_the_new_row_intact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    first = JsonlAuditSink(tmp_path)
    await first.write(rec())
    await first.write(rec())
    path = tmp_path / "audit-2026-10-03.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"id":3,"ts":"2026-10-03T12:')  # a crash mid-write

    second = JsonlAuditSink(tmp_path)
    await second.write(rec(reason="after the crash"))

    # The torn line is terminated, never rewritten; the new row is on its own line.
    raw = path.read_text(encoding="utf-8").splitlines()
    assert raw[2] == '{"id":3,"ts":"2026-10-03T12:'
    assert json.loads(raw[3])["id"] == 3
    with caplog.at_level(logging.WARNING, logger="app.sinks"):
        records = read_jsonl(tmp_path)
    assert [r.id for r in records] == [1, 2, 3]
    assert records[-1].reason == "after the crash"
    assert "unreadable line 3" in caplog.text


async def test_concurrent_tasks_write_exactly_n_valid_lines(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    n = 300
    await asyncio.gather(*(sink.write(rec(request_id=f"req-{i}")) for i in range(n)))

    rows = lines(tmp_path / "audit-2026-10-03.jsonl")
    assert len(rows) == n
    assert [r["id"] for r in rows] == list(range(1, n + 1))
    assert {r["request_id"] for r in rows} == {f"req-{i}" for i in range(n)}


def test_writers_on_several_threads_never_interleave(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    threads_n, per_thread = 8, 50

    def worker(t: int) -> None:
        async def run() -> None:
            for i in range(per_thread):
                await sink.write(rec(request_id=f"t{t}-{i}"))

        asyncio.run(run())

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = lines(tmp_path / "audit-2026-10-03.jsonl")
    assert len(rows) == threads_n * per_thread
    assert [r["id"] for r in rows] == list(range(1, threads_n * per_thread + 1))


# --- read_jsonl -----------------------------------------------------------------------------


async def test_read_jsonl_round_trip(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    written = [
        rec(ts="2026-10-03T10:00:00+00:00", action="block", reason="x", score=0.9),
        rec(ts="2026-10-03T11:00:00+00:00", check="jev", decided_by="fallback", tokens=5),
        rec(
            ts="2026-10-04T08:00:00+00:00", request_id=None, checkpoint=None, check="policy_loaded"
        ),
    ]
    for r in written:
        await sink.write(r)

    assert read_jsonl(tmp_path) == written


async def test_read_jsonl_since_filters_rows_and_files(tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    for ts in ("2026-10-02T23:00:00+00:00", "2026-10-03T10:00:00+00:00", "2026-10-03T12:00:00Z"):
        await sink.write(rec(ts=ts))

    got = read_jsonl(tmp_path, since="2026-10-03T11:00:00+00:00")
    assert [r.id for r in got] == [3]
    with pytest.raises(ValueError):
        read_jsonl(tmp_path, since="yesterday")


async def test_read_jsonl_skips_a_partial_trailing_line_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sink = JsonlAuditSink(tmp_path)
    await sink.write(rec())
    await sink.write(rec())
    with (tmp_path / "audit-2026-10-03.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"id":3,"ts":"2026-10-03T1')  # a row still being written

    with caplog.at_level(logging.WARNING, logger="app.sinks"):
        records = read_jsonl(tmp_path)
    assert [r.id for r in records] == [1, 2]
    assert caplog.text == ""


def test_read_jsonl_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "nope") == []


# --- FanoutAuditSink ------------------------------------------------------------------------


class FailingSink:
    def __init__(self) -> None:
        self.calls = 0

    async def write(self, record: AuditRecord) -> None:
        self.calls += 1
        raise ConnectionError("langfuse unreachable")


class RecordingSink:
    """Keeps what it receives without touching it."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        self.records.append(record)


async def test_secondary_failure_is_logged_and_isolated(caplog: pytest.LogCaptureFixture) -> None:
    primary, failing, other = MemoryAuditSink(), FailingSink(), MemoryAuditSink()
    fanout = FanoutAuditSink([primary, failing, other])

    with caplog.at_level(logging.ERROR, logger="app.sinks"):
        await fanout.write(rec(request_id="req-42"))
        await fanout.write(rec(request_id="req-43"))

    assert len(primary.records) == 2
    assert len(other.records) == 2
    assert failing.calls == 2
    assert "FailingSink" in caplog.text
    assert "req-42" in caplog.text
    assert "langfuse unreachable" in caplog.text


async def test_primary_failure_propagates_and_skips_the_rest() -> None:
    other = MemoryAuditSink()
    fanout = FanoutAuditSink([other, FailingSink()], primary=1)

    with pytest.raises(ConnectionError):
        await fanout.write(rec())
    assert other.records == []


async def test_each_sink_gets_its_own_copy_with_the_primary_id(tmp_path: Path) -> None:
    jsonl, memory, recorder = JsonlAuditSink(tmp_path), MemoryAuditSink(), RecordingSink()
    for _ in range(2):
        await jsonl.write(rec())  # the JSONL ids run ahead of the memory sink's own count
    fanout = FanoutAuditSink([jsonl, memory, recorder])
    original = rec()

    await fanout.write(original)

    assert original.id is None  # the caller's record is not touched
    [mem_copy], [rec_copy] = memory.records, recorder.records
    assert mem_copy is not rec_copy and mem_copy is not original
    assert mem_copy.id == 1  # MemoryAuditSink renumbered only its own copy
    assert rec_copy.id == 3  # the primary's (JSONL) id
    assert lines(tmp_path / "audit-2026-10-03.jsonl")[-1]["id"] == 3


def test_fanout_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError):
        FanoutAuditSink([])
    with pytest.raises(ValueError):
        FanoutAuditSink([MemoryAuditSink()], primary=1)
