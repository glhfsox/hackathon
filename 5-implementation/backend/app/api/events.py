import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from app.deps import AuditReaderDep
from app.models import AuditRecord

router = APIRouter(prefix="/api", tags=["events"])

# The live view polls the audit log the other endpoints read, so it shows exactly what is audited.
POLL_S = 0.5
# On first connect: the most recent rows of the last few minutes, so the view is not empty.
BACKLOG_ROWS = 300
BACKLOG_WINDOW = timedelta(minutes=15)
KEEPALIVE_S = 15.0


def pending(records: list[AuditRecord], *, after_id: int | None, backlog: int) -> list[AuditRecord]:
    """Rows to send: everything after the last id the client has, or the recent backlog."""
    if after_id is None:
        return records[-backlog:] if backlog else []
    return [r for r in records if r.id is not None and r.id > after_id]


def frame(record: AuditRecord) -> str:
    """One server-sent event; its id lets a reconnecting browser resume after it."""
    return f"id: {record.id}\ndata: {record.model_dump_json()}\n\n"


@router.get("/events")
async def events(
    request: Request,
    reader: AuditReaderDep,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Audit rows as they are written (text/event-stream), for the dashboard's live view."""
    after_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else None

    async def stream() -> AsyncIterator[str]:
        nonlocal after_id
        since = datetime.now(UTC) - BACKLOG_WINDOW
        idle = 0.0
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(reader.read, since.isoformat())
            new = pending(rows, after_id=after_id, backlog=BACKLOG_ROWS)
            for record in new:
                yield frame(record)
            if new:
                after_id = new[-1].id
                # Re-read only the recent tail; a little overlap is filtered out by id.
                since = datetime.fromisoformat(new[-1].ts.replace("Z", "+00:00")) - timedelta(
                    seconds=5
                )
                idle = 0.0
            elif after_id is None:
                after_id = 0  # an empty log: from now on send every new row
            idle += POLL_S
            if idle >= KEEPALIVE_S:
                yield ": keepalive\n\n"
                idle = 0.0
            await asyncio.sleep(POLL_S)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
