"""Audit sink interface. Append-only by construction: there is no read-modify or delete API."""

from __future__ import annotations

from typing import Protocol

from app.models import AuditRecord


class AuditSink(Protocol):
    async def write(self, record: AuditRecord) -> None: ...


class AuditReader(Protocol):
    """Reads the audit log back for the dashboard. Blocking: call it off the event loop."""

    def read(self, since: str | None = None) -> list[AuditRecord]:
        """Rows with `ts >= since` (ISO 8601), oldest first. Raises ValueError on a bad `since`."""
        ...
