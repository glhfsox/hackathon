"""Extra audit sinks for observability: a Power BI friendly JSONL log and a fan-out.

Every decision the control layer makes already reaches an AuditSink, so exporting it is a matter
of adding sinks; checks and the pipeline stay unchanged. See backend/docs/observability.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.audit import AuditSink
from app.metrics import AI_CHECK, SYSTEM_EVENTS
from app.models import Action, AuditRecord

log = logging.getLogger(__name__)

FILE_PREFIX = "audit-"
FILE_SUFFIX = ".jsonl"
DERIVED_COLUMNS = (
    "date",
    "hour",
    "minute",
    "is_block",
    "is_redact",
    "is_flag",
    "is_system_event",
    "category",
)
# Column order of a JSONL/CSV row: the AuditRecord fields, then the derived columns.
COLUMNS = (*AuditRecord.model_fields, *DERIVED_COLUMNS)

# Bytes read from the end of a file to find the last id on start; one row is far smaller.
_TAIL_BYTES = 64 * 1024
_SIGNATURES_CHECK = "signatures"
# Reason formats written by the checks: "signature <id> (<category>): <description>" and,
# for a Jev block, "<reason> [<category>, ...]".
_SIGNATURE_CATEGORY = re.compile(r"^signature \S+ \(([^()]+)\):")
_JEV_CATEGORIES = re.compile(r"\[([^\[\]]+)\]\s*$")


def _utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    # Same rule as app.metrics: the audit log writes UTC, a naive timestamp is read as UTC.
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _category(record: AuditRecord) -> str | None:
    """Best effort: the attack category named in a signatures or Jev reason, else None."""
    if record.check == _SIGNATURES_CHECK:
        match = _SIGNATURE_CATEGORY.match(record.reason)
    elif record.check == AI_CHECK:
        match = _JEV_CATEGORIES.search(record.reason)
    else:
        return None
    if match is None:
        return None
    # A Jev verdict can name several categories; one column value keeps Power BI grouping simple.
    return match.group(1).split(",")[0].strip() or None


def audit_row(record: AuditRecord) -> dict[str, Any]:
    """One flat row: the AuditRecord fields followed by the derived columns in DERIVED_COLUMNS."""
    ts = _utc(record.ts)
    row = record.model_dump(mode="json")
    row.update(
        date=ts.date().isoformat(),
        hour=ts.hour,
        # Same format as the `timeline` minutes of GET /api/metrics, so the two can be joined.
        minute=ts.strftime("%Y-%m-%dT%H:%M:00Z"),
        is_block=record.action == Action.block,
        is_redact=record.action == Action.redact,
        is_flag=record.action == Action.flag,
        # Same rule as app.metrics: a known system-event id, or no checkpoint.
        is_system_event=record.check in SYSTEM_EVENTS or record.checkpoint is None,
        category=_category(record),
    )
    return row


def _day_file(directory: Path, ts: str) -> Path:
    return directory / f"{FILE_PREFIX}{_utc(ts).date().isoformat()}{FILE_SUFFIX}"


def _audit_files(directory: Path) -> list[Path]:
    # ISO dates in the names sort chronologically.
    return sorted(directory.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}"))


def _last_id(directory: Path) -> int:
    """The id on the last complete row of the newest audit file that has one, else 0."""
    for path in reversed(_audit_files(directory)):
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - _TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            try:
                row = json.loads(line)
            except ValueError:
                # A torn last line, or the first line of the tail cut in half: look further up.
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                return row["id"]
    return 0


def _ends_torn(path: Path) -> bool:
    """True when the file's last line has no newline, e.g. after a crash mid-write."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except FileNotFoundError:
        return False


class JsonlAuditSink:
    """Appends one flat JSON object per line to `<directory>/audit-YYYY-MM-DD.jsonl`.

    The file is picked by the UTC day of the record's `ts`, so a new day starts a new file. Rows
    are `audit_row(record)`: the AuditRecord fields plus derived columns for Power BI. The sink
    assigns `record.id` (like MemoryAuditSink), continuing after the last id in the newest file,
    so ids keep increasing across restarts. One writing process per directory.

    Append-only: files are only ever opened in append mode; nothing is rewritten or truncated.
    A torn last line left by a crash is terminated with a newline before the next row, so the
    new row is never glued onto it.

    Blocking I/O: each write is a single append of one short line (well under 1 KB) to the OS
    page cache plus a flush, no fsync. That takes microseconds, so it runs inline instead of via
    asyncio.to_thread, whose thread hop costs more than the write and would sit on the request
    path (the pipeline awaits every audit write). Flushing per row means a process crash loses
    nothing already written; a power loss can lose rows the OS has not synced yet.

    The lock is a threading.Lock: nothing is awaited while it is held, and it also serialises
    callers on other threads or event loops, so id order always matches line order.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last = _last_id(self._dir)
        # Files already checked for a torn last line by this sink.
        self._checked: set[Path] = set()

    async def write(self, record: AuditRecord) -> None:
        path = _day_file(self._dir, record.ts)
        with self._lock:
            record.id = self._last + 1
            line = json.dumps(audit_row(record), ensure_ascii=False, separators=(",", ":"))
            prefix = "" if path in self._checked else ("\n" if _ends_torn(path) else "")
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(f"{prefix}{line}\n")
                    f.flush()
            except OSError:
                # The append may have been partial: check the file again before the next row.
                self._checked.discard(path)
                raise
            self._checked.add(path)
            self._last = record.id


class FanoutAuditSink:
    """Writes every record to several sinks; one of them is the audit of record.

    The primary sink is written first. If it fails the error propagates and the other sinks are
    skipped: a decision that is not in the audit log must not go through. A failure in any other
    sink (e.g. Langfuse unreachable) is logged with context and does not stop the remaining sinks
    or the request.

    Each sink gets its own copy of the record, because sinks such as MemoryAuditSink assign `id`
    on the object they receive. The secondary copies are taken from the primary's copy after it
    was written, so they carry the primary's id (the same id in the JSONL file and in Langfuse).
    """

    def __init__(self, sinks: list[AuditSink], primary: int = 0) -> None:
        if not sinks:
            raise ValueError("FanoutAuditSink needs at least one sink")
        if not 0 <= primary < len(sinks):
            raise ValueError(f"primary index {primary} is outside the {len(sinks)} sinks")
        self._sinks = list(sinks)
        self._primary = primary

    async def write(self, record: AuditRecord) -> None:
        written = record.model_copy()
        await self._sinks[self._primary].write(written)
        for index, sink in enumerate(self._sinks):
            if index == self._primary:
                continue
            try:
                await sink.write(written.model_copy())
            except Exception:
                log.exception(
                    "secondary audit sink %s failed for audit id %s (check %s, request %s); "
                    "the record is kept by the primary sink",
                    type(sink).__name__,
                    written.id,
                    written.check,
                    written.request_id,
                )


def _parse_row(line: str) -> AuditRecord:
    row = json.loads(line)
    if not isinstance(row, dict):
        raise ValueError(f"expected a JSON object, got {type(row).__name__}")
    # Derived columns are recomputed on demand, so only the AuditRecord fields are read back.
    fields = {k: v for k, v in row.items() if k in AuditRecord.model_fields}
    return AuditRecord.model_validate(fields)


def _read_file(path: Path) -> list[AuditRecord]:
    lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
    # After the last newline: "" when the file is complete, else a row still being written.
    last = len(lines) - 1
    records = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            records.append(_parse_row(line))
        except ValueError as exc:
            # pydantic's ValidationError is a ValueError too.
            if number - 1 == last:
                log.debug("skipping the unterminated last line of %s: %s", path, exc)
            else:
                log.warning("skipping unreadable line %d of %s: %s", number, path, exc)
    return records


def read_jsonl(directory: Path, since: str | None = None) -> list[AuditRecord]:
    """Load the audit rows written by JsonlAuditSink, oldest file first.

    `since` (ISO 8601) keeps rows with `ts >= since` and skips files of earlier days; it raises
    ValueError when it is not ISO 8601. A row still being written (an unterminated last line) is
    skipped. A missing directory gives an empty list.
    """
    since_dt = _utc(since) if since else None
    first_day = since_dt.date().isoformat() if since_dt else ""
    records: list[AuditRecord] = []
    for path in _audit_files(directory):
        if path.name[len(FILE_PREFIX) : -len(FILE_SUFFIX)] < first_day:
            continue
        records.extend(r for r in _read_file(path) if since_dt is None or _utc(r.ts) >= since_dt)
    return records
