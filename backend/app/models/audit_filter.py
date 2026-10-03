from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import Action, Checkpoint


class AuditFilter(BaseModel):
    """Filters for listing the audit log. Mirrors the query params of GET /api/audit."""

    caller_id: str | None = None
    check: str | None = None
    action: Action | None = None
    checkpoint: Checkpoint | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
