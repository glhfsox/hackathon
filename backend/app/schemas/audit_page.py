from pydantic import BaseModel

from app.models import AuditRecord


class AuditPage(BaseModel):
    """GET /api/audit: `total` rows match the filters, `items` is the requested page of them."""

    total: int
    items: list[AuditRecord]
