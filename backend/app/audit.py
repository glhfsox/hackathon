"""Audit sink interface. Append-only by construction: there is no read-modify or delete API.

The SQLite sink (docs/architecture.md §8) implements the same protocol; this module only ships the
in-memory sink used by tests and as the default until SQLite lands.
"""

from __future__ import annotations

from typing import Protocol

from app.models import AuditRecord


class AuditSink(Protocol):
    async def write(self, record: AuditRecord) -> None: ...


class MemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        record.id = len(self.records) + 1
        self.records.append(record)
