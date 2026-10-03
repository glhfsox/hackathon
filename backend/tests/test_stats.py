import asyncio
import csv
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.policy_store import parse_policy
from app.models import AuditRecord
from app.models.policy import Policy
from app.observability.sinks import COLUMNS, JsonlAuditSink
from app.observability.stats import (
    HISTORY_FILE,
    LATEST_FILE,
    StatsExporter,
    main,
    write_metrics_snapshot,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
POLICY_PATH = BACKEND_DIR / "policy.yaml"
NOW = datetime(2026, 10, 3, 12, 30, tzinfo=UTC)


@pytest.fixture
def policy() -> Policy:
    return parse_policy(POLICY_PATH.read_text(encoding="utf-8"))


def rec(request_id: str | None, check: str, action: str = "allow", **kw: Any) -> AuditRecord:
    fields: dict[str, Any] = {
        "ts": "2026-10-03T12:00:00+00:00",
        "request_id": request_id,
        "caller_id": "demo",
        "model": "gemma4",
        "checkpoint": "input",
        "check": check,
        "action": action,
        "reason": "r",
        "latency_ms": 2.0,
        "policy_version": "0.1+abcd1234",
    }
    fields.update(kw)
    return AuditRecord(**fields)


RECORDS = [
    rec("a", "pii_secrets", "redact", latency_ms=1.0),
    rec("a", "jev", "allow", latency_ms=100.0, decided_by="jev"),
    rec("b", "signatures", "block", reason="signature PI-001 (prompt_injection): override"),
    rec("c", "pii_secrets", checkpoint="output", tokens=40, cost=0.0),
    rec(None, "policy_loaded", caller_id=None, checkpoint=None),
]


def test_snapshot_writes_latest_and_appends_history(policy: Policy, tmp_path: Path) -> None:
    out = tmp_path / "out"
    path = write_metrics_snapshot(RECORDS, policy, out, now=NOW)
    write_metrics_snapshot(RECORDS[:1], policy, out, now=NOW.replace(minute=31))

    assert path == out / LATEST_FILE
    latest = json.loads(path.read_text())
    # The second snapshot replaced the first.
    assert latest["generated_at"] == "2026-10-03T12:31:00+00:00"
    assert latest["totals"]["requests"] == 1

    history = [json.loads(line) for line in (out / HISTORY_FILE).read_text().splitlines()]
    assert len(history) == 2
    first = history[0]
    assert first["ts"] == "2026-10-03T12:30:00+00:00"
    assert (first["requests"], first["allowed"], first["redacted"], first["blocked"]) == (
        3,
        1,
        1,
        1,
    )
    assert first["tokens_total"] == 40
    assert first["jev_latency_p50_ms"] == 100.0
    assert first["overhead_p95_ms"] == 101.0
    assert first["rules_overhead_p95_ms"] == 2.0
    assert all(not isinstance(v, dict | list) for v in first.values())
    # No temp files left behind.
    assert sorted(p.name for p in out.iterdir()) == [HISTORY_FILE, LATEST_FILE]


def test_snapshot_replace_is_atomic(
    policy: Policy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_metrics_snapshot(RECORDS, policy, tmp_path, now=NOW)
    before = (tmp_path / LATEST_FILE).read_text()

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_metrics_snapshot(RECORDS[:1], policy, tmp_path, now=NOW)

    # The old snapshot is intact and the temp file is gone.
    assert (tmp_path / LATEST_FILE).read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [HISTORY_FILE, LATEST_FILE]


async def _wait_for(path: Path, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not path.exists():
        assert asyncio.get_running_loop().time() < deadline, f"{path} never appeared"
        await asyncio.sleep(0.01)


async def test_exporter_loop_writes_todays_snapshot(policy: Policy, tmp_path: Path) -> None:
    sink = JsonlAuditSink(tmp_path)
    today = datetime.now(UTC).isoformat()
    await sink.write(rec("old", "pii_secrets", ts="2020-01-01T00:00:00+00:00"))
    await sink.write(rec("x", "pii_secrets", "redact", ts=today))
    await sink.write(rec("y", "signatures", "block", ts=today))

    exporter = StatsExporter(tmp_path, lambda: policy, interval_s=0.01)
    exporter.start()
    try:
        await _wait_for(tmp_path / HISTORY_FILE)
    finally:
        await exporter.stop()

    latest = json.loads((tmp_path / LATEST_FILE).read_text())
    # Only today's rows count.
    assert latest["totals"]["requests"] == 2
    assert latest["totals"]["blocked"] == 1


async def test_exporter_survives_a_failed_export(
    policy: Policy, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def flaky_policy() -> Policy:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("policy store not ready")
        return policy

    exporter = StatsExporter(tmp_path, flaky_policy, interval_s=0.01)
    with caplog.at_level(logging.ERROR, logger="app.observability.stats"):
        exporter.start()
        try:
            await _wait_for(tmp_path / LATEST_FILE)
        finally:
            await exporter.stop()

    assert "metrics export" in caplog.text
    assert "policy store not ready" in caplog.text
    assert json.loads((tmp_path / LATEST_FILE).read_text())["totals"]["requests"] == 0


def test_exporter_rejects_a_bad_interval(policy: Policy, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        StatsExporter(tmp_path, lambda: policy, interval_s=0)


async def _write_logs(directory: Path) -> None:
    sink = JsonlAuditSink(directory)
    for record in RECORDS:
        await sink.write(record.model_copy())


async def test_cli_prints_metrics_and_writes_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = tmp_path / "logs"
    await _write_logs(logs)
    out_csv = tmp_path / "audit.csv"

    code = main(["--logs", str(logs), "--policy", str(POLICY_PATH), "--csv", str(out_csv)])

    captured = capsys.readouterr()
    assert code == 0
    metrics = json.loads(captured.out)
    assert metrics["totals"] == {
        "requests": 3,
        "allowed": 1,
        "redacted": 1,
        "blocked": 1,
        "flagged": 0,
    }
    assert metrics["system_events"] == {"policy_loaded": 1}
    assert f"wrote {len(RECORDS)} audit rows" in captured.err

    with out_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == list(COLUMNS)
    assert len(rows) == len(RECORDS)
    assert rows[2]["category"] == "prompt_injection"
    assert rows[2]["is_block"] == "true"
    assert rows[4]["is_system_event"] == "true"
    assert rows[4]["checkpoint"] == ""


async def test_cli_since_narrows_metrics_and_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = tmp_path / "logs"
    await _write_logs(logs)
    sink = JsonlAuditSink(logs)
    await sink.write(rec("late", "pii_secrets", ts="2026-10-03T13:00:00+00:00"))
    out_csv = tmp_path / "late.csv"

    since = "2026-10-03T12:30:00+00:00"
    code = main(
        ["--logs", str(logs), "--policy", str(POLICY_PATH), "--since", since, "--csv", str(out_csv)]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["totals"]["requests"] == 1
    assert len(out_csv.read_text().splitlines()) == 2  # header + one row


TURN_RECORDS = [
    rec("a", "turn_summary", "redact", conversation_id="s1", overhead_ms=101.0, step=0),
    rec("b", "turn_summary", "block", conversation_id="s1", overhead_ms=2.0, step=1,
        blocked_by="signatures"),
    rec("c", "turn_summary", checkpoint="output", conversation_id="s2", tokens=40, cost=0.0,
        prompt_tokens=30, completion_tokens=10, tool_calls=0, overhead_ms=2.0,
        upstream_latency_ms=80.0),
]  # fmt: skip


def test_history_line_has_the_agent_and_economic_numbers(policy: Policy, tmp_path: Path) -> None:
    write_metrics_snapshot(RECORDS + TURN_RECORDS, policy, tmp_path, now=NOW)

    [line] = [json.loads(x) for x in (tmp_path / HISTORY_FILE).read_text().splitlines()]
    assert (line["sessions"], line["turns"], line["blocked_before_upstream"]) == (2, 3, 1)
    assert (line["tokens_total"], line["cost_total"]) == (40, 0.0)
    # The turn rows change none of the request totals.
    assert (line["requests"], line["blocked"]) == (3, 1)
    assert all(not isinstance(v, dict | list) for v in line.values())


async def test_cli_turns_csv_has_only_turn_summary_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = tmp_path / "logs"
    sink = JsonlAuditSink(logs)
    for record in RECORDS + TURN_RECORDS:
        await sink.write(record.model_copy())
    turns_csv, all_csv = tmp_path / "turns.csv", tmp_path / "audit.csv"

    code = main(
        ["--logs", str(logs), "--policy", str(POLICY_PATH), "--csv", str(all_csv),
         "--turns-csv", str(turns_csv)]
    )  # fmt: skip

    captured = capsys.readouterr()
    assert code == 0
    assert "wrote 3 turn_summary rows" in captured.err
    assert json.loads(captured.out)["agents"]["demo"]["turns"] == 3
    with turns_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == list(COLUMNS)
    assert [r["request_id"] for r in rows] == ["a", "b", "c"]
    assert {r["check"] for r in rows} == {"turn_summary"}
    assert [r["conversation_id"] for r in rows] == ["s1", "s1", "s2"]
    assert rows[1]["blocked_by"] == "signatures"
    assert (rows[2]["prompt_tokens"], rows[2]["upstream_latency_ms"]) == ("30", "80.0")
    # The full CSV has the agent columns too, empty on check rows.
    with all_csv.open(newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    assert len(all_rows) == len(RECORDS) + len(TURN_RECORDS)
    assert all_rows[0]["conversation_id"] == "" and all_rows[-1]["conversation_id"] == "s2"


def test_cli_reports_errors_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--logs", str(tmp_path), "--policy", str(tmp_path / "missing.yaml")])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "missing.yaml" in captured.err


async def test_cli_runs_as_a_module(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    await _write_logs(logs)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.observability.stats",
            "--logs",
            str(logs),
            "--policy",
            str(POLICY_PATH),
        ],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["totals"]["requests"] == 3
