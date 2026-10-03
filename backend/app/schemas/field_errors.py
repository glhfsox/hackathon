from pydantic import BaseModel

from app.models import PolicyError


class FieldErrors(BaseModel):
    """422 body of the dashboard API: a rejected policy, or a query value that does not parse."""

    errors: list[PolicyError]
