from pydantic import BaseModel, Field

from app.models import PolicyError


class PolicyValidation(BaseModel):
    valid: bool
    errors: list[PolicyError] = Field(default_factory=list)
