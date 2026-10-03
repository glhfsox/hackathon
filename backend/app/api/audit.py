import asyncio
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.errors import unprocessable
from app.deps import AuditReaderDep
from app.models import Action, AuditRecord, Checkpoint, PolicyError
from app.observability.stats import write_csv
from app.schemas.audit_page import AuditPage
from app.schemas.field_errors import FieldErrors

router = APIRouter(prefix="/api/audit", tags=["audit"])

_422: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": FieldErrors}
}


def parse_utc(value: str) -> datetime:
    """ISO 8601 to an aware UTC datetime; a naive value is read as UTC, as the audit log does.
    Raises ValueError."""
    dt = datetime.fromisoformat(value)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class AuditFilters:
    """The query filters of GET /api/audit and /api/audit/export (contracts/http-api.md)."""

    caller_id: str | None = None
    check: str | None = None
    action: Action | None = None
    checkpoint: Checkpoint | None = None
    since: str | None = None
    until: str | None = None


Filters = Annotated[AuditFilters, Depends()]


async def _matching(
    reader: AuditReaderDep, filters: AuditFilters
) -> list[AuditRecord] | JSONResponse:
    """The rows that pass every filter, oldest first, or a 422 for a bad since/until."""
    bounds: dict[str, datetime] = {}
    for name, value in (("since", filters.since), ("until", filters.until)):
        if value is None:
            continue
        try:
            bounds[name] = parse_utc(value)
        except ValueError as exc:
            return unprocessable([PolicyError(loc=name, msg=f"not ISO 8601: {exc}")])
    until = bounds.get("until")
    # The reader is blocking file I/O; `since` also lets it skip the files of earlier days.
    records = await asyncio.to_thread(reader.read, filters.since)
    return [
        r
        for r in records
        if (filters.caller_id is None or r.caller_id == filters.caller_id)
        and (filters.check is None or r.check == filters.check)
        and (filters.action is None or r.action == filters.action)
        and (filters.checkpoint is None or r.checkpoint == filters.checkpoint)
        and (until is None or parse_utc(r.ts) <= until)
    ]


@router.get("", response_model=AuditPage, responses=_422)
async def list_audit(
    reader: AuditReaderDep,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage | JSONResponse:
    """Newest first, so the first page is what just happened."""
    rows = await _matching(reader, filters)
    if isinstance(rows, JSONResponse):
        return rows
    rows.reverse()
    return AuditPage(total=len(rows), items=rows[offset : offset + limit])


def _csv(records: list[AuditRecord]) -> str:
    # The same columns as the JSONL log and `python -m app.observability.stats --csv`.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.csv"
        write_csv(records, path)
        return path.read_text(encoding="utf-8")


@router.get("/export", response_class=Response, responses=_422)
async def export_audit(
    reader: AuditReaderDep, filters: Filters, format: Literal["json", "csv"] = "json"
) -> Response:
    """Every matching row, oldest first, as a file download."""
    rows = await _matching(reader, filters)
    if isinstance(rows, JSONResponse):
        return rows
    if format == "csv":
        content = await asyncio.to_thread(_csv, rows)
        media_type = "text/csv"
    else:
        content = json.dumps([r.model_dump(mode="json") for r in rows], ensure_ascii=False)
        media_type = "application/json"
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="audit.{format}"'},
    )
