from pydantic import BaseModel

from app.models import Decision


class ToolCheckResponse(BaseModel):
    allowed: bool
    decision: Decision
