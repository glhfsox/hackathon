from pathlib import Path

from app.models import AuditRecord
from app.observability.sinks import read_jsonl


class JsonlAuditReader:
    """The AuditReader over the JSONL files JsonlAuditSink writes.

    docs/architecture.md §8 names an SQLite table; it is not built, so the JSONL log is the audit
    of record. Swapping the store means replacing this class only.
    """

    def __init__(self, logs_dir: Path) -> None:
        self._logs_dir = logs_dir

    def read(self, since: str | None = None) -> list[AuditRecord]:
        return read_jsonl(self._logs_dir, since)
