import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.audit import parse_utc
from app.api.errors import unprocessable
from app.core.metrics import compute_metrics
from app.deps import AuditReaderDep, PolicyProviderDep
from app.models import PolicyError
from app.schemas.field_errors import FieldErrors

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get(
    "/metrics",
    response_model=None,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": FieldErrors}},
)
async def metrics(
    policy: PolicyProviderDep, reader: AuditReaderDep, since: str | None = None
) -> dict[str, Any] | JSONResponse:
    """The body of contracts/http-api.md plus the additive fields of app.core.metrics."""
    snapshot = policy.current()
    now = datetime.now(UTC)
    try:
        since_dt = parse_utc(since) if since else None
    except ValueError as exc:
        return unprocessable([PolicyError(loc="since", msg=f"not ISO 8601: {exc}")])
    # The budgets always cover today (UTC), so today's rows are read whatever `since` says.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    read_from = min(since_dt, day_start) if since_dt else None
    records = await asyncio.to_thread(reader.read, read_from.isoformat() if read_from else None)
    return compute_metrics(records, snapshot.policy, since=since, now=now)
