"""Metrics snapshots for dashboards and Power BI, plus a small CLI.

`write_metrics_snapshot` turns audit records into `metrics-latest.json` (the GET /api/metrics body
plus `generated_at`, replaced atomically) and appends one flat line to `metrics-history.jsonl`
(the numbers a time chart needs). `StatsExporter` refreshes both every few seconds from the JSONL
audit log of the current UTC day. See backend/docs/observability.md.

CLI: python -m app.observability.stats --logs logs/ --policy policy.yaml [--since ISO]
     [--csv out.csv] [--turns-csv turns.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.metrics import compute_metrics
from app.core.policy_store import parse_policy
from app.models import TURN_SUMMARY, AuditRecord
from app.models.policy import Policy
from app.observability.sinks import COLUMNS, audit_row, read_jsonl
from app.protocols.policy_provider import PolicyRejectedError

log = logging.getLogger(__name__)

LATEST_FILE = "metrics-latest.json"
HISTORY_FILE = "metrics-history.jsonl"


def _atomic_write(path: Path, text: str) -> None:
    """Temp file in the same directory, then os.replace: a reader (Power BI, a dashboard) sees
    the old snapshot or the new one, never half of one. No fsync: the file is derived data."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        # Gone after a successful replace; cleans up after a failed one.
        tmp.unlink(missing_ok=True)


def history_row(metrics: dict[str, Any], generated_at: str) -> dict[str, Any]:
    """The flat top-level numbers of one metrics body, one column each."""
    return {
        "ts": generated_at,
        "active_profile": metrics["active_profile"],
        **metrics["totals"],
        "tokens_total": metrics["tokens_total"],
        "cost_total": metrics["cost_total"],
        "overhead_p50_ms": metrics["overhead_ms"]["p50"],
        "overhead_p95_ms": metrics["overhead_ms"]["p95"],
        "rules_overhead_p50_ms": metrics["rules_overhead_ms"]["p50"],
        "rules_overhead_p95_ms": metrics["rules_overhead_ms"]["p95"],
        "jev_latency_p50_ms": metrics["jev_latency_ms"]["p50"],
        "jev_latency_p95_ms": metrics["jev_latency_ms"]["p95"],
        # A conversation_id includes the caller, so per-caller sessions add up without overlap.
        "sessions": sum(a["sessions"] for a in metrics["agents"].values()),
        "turns": sum(a["turns"] for a in metrics["agents"].values()),
        "blocked_before_upstream": metrics["economics"]["blocked_before_upstream"],
    }


def write_metrics_snapshot(
    records: Iterable[AuditRecord],
    policy: Policy,
    out_dir: Path,
    now: datetime | None = None,
) -> Path:
    """Write metrics-latest.json atomically and append a line to metrics-history.jsonl.

    Returns the path of metrics-latest.json. The history file is append-only.
    """
    now = now or datetime.now(UTC)
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    metrics = compute_metrics(records, policy, now=now)
    generated_at = now.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    latest = out_dir / LATEST_FILE
    _atomic_write(latest, json.dumps({"generated_at": generated_at, **metrics}, indent=2))
    with (out_dir / HISTORY_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(history_row(metrics, generated_at)) + "\n")
    return latest


class StatsExporter:
    """Every `interval_s`, recompute the metrics of the current UTC day from the JSONL audit log
    in `logs_dir` and write the snapshot files to `out_dir` (default: `logs_dir`).

    `policy` returns the policy in force, e.g. `lambda: store.current().policy`, so hot reloads
    show up in the snapshot. A failed export is logged and retried on the next tick.
    """

    def __init__(
        self,
        logs_dir: Path,
        policy: Callable[[], Policy],
        *,
        out_dir: Path | None = None,
        interval_s: float = 5.0,
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s must be positive, got {interval_s}")
        self._logs_dir = logs_dir
        self._policy = policy
        self._out_dir = out_dir or logs_dir
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def export_once(self, now: datetime | None = None) -> Path:
        now = now or datetime.now(UTC)
        day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        records = read_jsonl(self._logs_dir, since=day_start.isoformat())
        return write_metrics_snapshot(records, self._policy(), self._out_dir, now=now)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("stats exporter already started")
        self._task = asyncio.create_task(self._loop(), name="stats-export")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            try:
                # Reading a day of audit rows and aggregating them is blocking work: it runs in a
                # worker thread so requests on the event loop are not held up.
                await asyncio.to_thread(self.export_once)
            except Exception:
                log.exception(
                    "metrics export from %s failed; retrying in %ss",
                    self._logs_dir,
                    self._interval_s,
                )
            await asyncio.sleep(self._interval_s)


def _csv_value(value: Any) -> Any:
    # Lowercase like the JSONL rows; Power BI reads true/false as booleans.
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def write_csv(records: Iterable[AuditRecord], path: Path) -> int:
    """Write the audit rows as a flat CSV with the JSONL columns. Returns the row count."""
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow({k: _csv_value(v) for k, v in audit_row(record).items()})
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.observability.stats",
        description="Print control-layer metrics (JSON) from the JSONL audit log.",
    )
    parser.add_argument("--logs", type=Path, required=True, help="directory of audit-*.jsonl")
    parser.add_argument("--policy", type=Path, required=True, help="policy YAML file")
    parser.add_argument("--since", help="ISO 8601 timestamp; only rows at or after it count")
    parser.add_argument("--csv", type=Path, help="also write the audit rows to this CSV file")
    parser.add_argument(
        "--turns-csv",
        type=Path,
        help=f"also write only the {TURN_SUMMARY} rows (agent and economic facts) to this CSV",
    )
    args = parser.parse_args(argv)

    try:
        policy = parse_policy(args.policy.read_text(encoding="utf-8"))
        # All rows: budget_by_caller always covers today, whatever `since` says.
        records = read_jsonl(args.logs)
        metrics = compute_metrics(records, policy, since=args.since)
        wants_csv = args.csv is not None or args.turns_csv is not None
        rows = read_jsonl(args.logs, since=args.since) if args.since and wants_csv else records
        if args.csv is not None:
            count = write_csv(rows, args.csv)
            print(f"wrote {count} audit rows to {args.csv}", file=sys.stderr)
        if args.turns_csv is not None:
            turns = [r for r in rows if r.check == TURN_SUMMARY]
            count = write_csv(turns, args.turns_csv)
            print(f"wrote {count} {TURN_SUMMARY} rows to {args.turns_csv}", file=sys.stderr)
    except (PolicyRejectedError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # stdout carries only the metrics JSON, so it can be piped into a file or jq.
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
