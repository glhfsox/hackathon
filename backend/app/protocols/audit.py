"""Audit sink interface. Append-only by construction: there is no read-modify or delete API."""

from __future__ import annotations

from typing import Protocol

from app.models import AuditRecord


class AuditSink(Protocol):
    async def write(self, record: AuditRecord) -> None: ...
